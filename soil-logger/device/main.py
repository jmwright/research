"""
main.py -- NodeMCU Amica (ESP8266 / MicroPython) soil logger + TCP server.

Every SAMPLE_INTERVAL_S it reads the Adafruit STEMMA (Seesaw) soil sensor's
moisture and temperature, appends a timestamped line to a CSV file on flash,
and serves the whole file to any client that connects on TCP_PORT. A tiny
Python client (client/download.py) pulls it to your laptop.

Time: there are two clocks on this board and each is trusted for one thing.

  * time.time() is the RTC, disciplined by NTP. It is accurate in absolute
    terms, which is what makes timestamps real Unix seconds (UTC) and lets the
    visualizer compute phase. But it STEPS when a sync lands -- forwards and
    sometimes backwards -- so it must never drive scheduling.
  * time.ticks_ms() is monotonic and never steps, so it is what schedules
    samples. That is the invariant the visualizer leans on: file order is
    acquisition order, and the timestamps are the channel that can lie.

The catch is that ticks_ms() is monotonic but its RATE is only as good as the
crystal setting in the flash header. On 2026-09-15 this board rebooted and came
back with the SDK converting timers as though its 26 MHz part were 40 MHz;
ticks then ran slow by 26/40 and a nominal 5 minute interval quietly became
7m40s of real time. Nothing in the log said so -- you could only see it by
noticing the deltas had stretched by exactly 40/26. So we now measure the tick
rate against NTP-true anchors and schedule in corrected ticks, and we write the
measurement into the log where it can be read back.

Log format: data rows stay exactly three columns, `unix_time,moisture,temp_c`.
Event markers are written as comment rows beginning with '#'. Parsers that turn
the first field into a number and skip non-numbers (viz/public/rings.js does)
ignore them for free, so the markers cost nothing downstream.

Copy config_example.py to config.py and fill in your WiFi before flashing.
Files to put on the board: main.py, config.py.
"""
import time
import socket
import machine

try:
    import network
    import ntptime
except ImportError:
    network = None            # lets this file be import-checked off-device

import config

# ---- hardware config ----
I2C_SCL = 5                   # NodeMCU D1
I2C_SDA = 4                   # NodeMCU D2
SEESAW_ADDR = 0x36
SAMPLE_INTERVAL_S = 300       # 5 minutes between readings

# Each logged row is the MEDIAN of a burst of reads, not a single read.
# Measured on 4,584 rows of the real log, one read per sample gives a robust
# per-sample noise of 17.8 counts -- against a late-cycle "break" signal of only
# 8-20 counts, i.e. the thing we are trying to see was under the noise floor.
# A median of N reads cuts that by 1.253/sqrt(N): N=15 takes 17.8 -> 5.8.
#
# Median, not mean, because the seesaw touch read glitches HIGH: 5.6% of samples
# sit >45 counts above trend and they are overwhelmingly isolated single reads.
# A mean drags those in; a median discards them.
#
# The reads are SPACED rather than taken back to back. A glitch with a
# correlation time longer than the burst would otherwise corrupt every read in
# it, and a tight burst would measure the glitch instead of averaging it away.
# 15 x 50 ms spans ~0.8 s, which is 0.3% duty at a 300 s interval.
SAMPLE_READS = 15             # odd, so the median of a full burst is integral
SAMPLE_READ_GAP_MS = 50       # spacing between reads within one burst
NTP_RETRY_S = 30              # while unsynced, retry the clock this often
NTP_RESYNC_S = 24 * 3600      # re-sync the clock once a day, once calibrated
NTP_CAL_S = 3600              # ...but hourly until the tick rate is measured
NET_CHECK_S = 60              # how often to look at the WiFi link
TCP_PORT = getattr(config, "TCP_PORT", 8266)
LOG_PATH = "soil_log.csv"
UNIX_OFFSET = 946684800       # seconds between 1970-01-01 and 2000-01-01

# ---- timebase calibration ----
# Measured ticks_ms() units per real second. Nominally 1000. A wrong crystal
# setting shows up here as roughly 650 (26/40) or 1538 (40/26).
CAL_MIN_WINDOW_S = 600        # too short a window and NTP jitter dominates
CAL_MIN_RATE = 400.0          # sanity bounds; outside these we keep the old
CAL_MAX_RATE = 2500.0         # rate rather than trust a wild measurement

tick_rate = 1000.0
calibrated = False
_anchor_ticks = None          # ticks at the last NTP-true anchor
_anchor_rtc = None            # RTC seconds at that same anchor

i2c = machine.SoftI2C(scl=machine.Pin(I2C_SCL), sda=machine.Pin(I2C_SDA))


# ---- Seesaw reads ----
def median(xs):
    """Median of a list, or None if it is empty. No statistics module here."""
    v = sorted(xs)
    n = len(v)
    if not n:
        return None
    h = n // 2
    return v[h] if n % 2 else (v[h - 1] + v[h]) / 2


def read_moisture_once():
    try:
        i2c.writeto(SEESAW_ADDR, bytes([0x0F, 0x10]))   # TOUCH base, channel 0
        time.sleep_ms(5)
        d = i2c.readfrom(SEESAW_ADDR, 2)
        v = (d[0] << 8) | d[1]
        return None if v > 4095 else v
    except OSError:
        return None


def read_temp_c_once():
    try:
        i2c.writeto(SEESAW_ADDR, bytes([0x00, 0x04]))   # STATUS base, TEMP
        time.sleep_ms(5)
        d = i2c.readfrom(SEESAW_ADDR, 4)
        raw = (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]
        return raw / 65536.0
    except OSError:
        return None


def read_sample():
    """One logged reading: the median of a spaced burst of SAMPLE_READS reads.

    Moisture and temperature are interleaved so both describe the same window
    rather than two windows a second apart. Failed reads are dropped rather
    than counted, so a burst that loses a few to I2C errors still yields a
    median from what survived; only a burst that loses every read gives None,
    which append_reading() writes as an empty field exactly as before.

    Returns (moisture, temp_c, spread), where spread is the peak-to-peak of the
    surviving moisture reads. Spread is a diagnostic for the serial console --
    it is the instantaneous noise, measured every sample -- and is deliberately
    NOT written to the CSV, which keeps the three-column schema every existing
    parser and analysis depends on.
    """
    ms = []
    cs = []
    for i in range(SAMPLE_READS):
        m = read_moisture_once()
        if m is not None:
            ms.append(m)
        c = read_temp_c_once()
        if c is not None:
            cs.append(c)
        if i + 1 < SAMPLE_READS:
            time.sleep_ms(SAMPLE_READ_GAP_MS)

    m = median(ms)
    # Keep the CSV column integral. A burst that lost an even number of reads
    # would otherwise land on a .5, and half a count is far below the ~5.8 the
    # median itself is worth -- not worth a schema wobble to keep.
    if m is not None:
        m = int(round(m))
    spread = (max(ms) - min(ms)) if ms else None
    return m, median(cs), spread


# ---- timebase ----
def ticks_for(seconds):
    """Real seconds -> ticks_ms() units, using the measured rate.

    Every scheduling deadline goes through here. With a correct crystal this is
    just seconds*1000; with a wrong one it is what keeps the sample interval
    pinned to real time anyway.
    """
    return int(seconds * tick_rate)


def note_sync_anchor():
    """Record an NTP-true anchor and, given two of them, measure the tick rate.

    Called immediately after a successful sync, when the RTC is known good. The
    real time between two consecutive anchors is the difference of two true
    clock readings, so free-running RTC drift between syncs never enters the
    measurement -- only the ticks elapsed over a known-real interval do.
    """
    global _anchor_ticks, _anchor_rtc, tick_rate, calibrated
    now_t = time.ticks_ms()
    now_r = time.time()
    if _anchor_ticks is not None:
        real = now_r - _anchor_rtc
        tks = time.ticks_diff(now_t, _anchor_ticks)
        if real >= CAL_MIN_WINDOW_S and tks > 0:
            rate = tks / float(real)
            if CAL_MIN_RATE <= rate <= CAL_MAX_RATE:
                tick_rate = rate
                calibrated = True
                print("cal: %.1f ticks/s over %d s" % (rate, real))
                log_event("cal", "%.1f,%d" % (rate, real))
            else:
                # Out of range means something we do not understand; say so in
                # the log and keep scheduling on the rate we already had.
                print("cal: rejected %.1f ticks/s" % rate)
                log_event("cal", "rejected,%.1f,%d" % (rate, real))
    _anchor_ticks = now_t
    _anchor_rtc = now_r


# ---- networking ----
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(40):
            if wlan.isconnected():
                break
            time.sleep(0.5)
    print("wifi:", wlan.ifconfig()[0] if wlan.isconnected() else "FAILED")
    return wlan


def net_check(wlan, known_ip):
    """Re-associate if the link dropped, and record the address when it moves.

    A reboot or a re-lease moves the IP, and the only place it was ever
    announced was the serial console -- which is not attached to a pot on a
    windowsill. Writing it into the log means the next download carries the
    answer, and the download after a move can be aimed without a subnet sweep.
    """
    if wlan is None:
        return known_ip
    if not wlan.isconnected():
        try:
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        except OSError:
            pass
        return known_ip
    ip = wlan.ifconfig()[0]
    if ip != known_ip:
        print("ip:", ip)
        log_event("net", ip)
    return ip


# Public NTP servers as raw IPs first (work even when DNS is broken), then a
# hostname as a last resort for networks that do resolve. The -2 error you can
# hit is a DNS failure -- some access points/hotspots hand out no working DNS
# server, which kills name lookup while numeric-IP traffic still works.
NTP_HOSTS = ("216.239.35.0",    # time.google.com  (confirmed working here)
             "129.6.15.28",     # time.nist.gov
             "162.159.200.1",   # time.cloudflare.com
             "pool.ntp.org")    # name fallback (needs working DNS)


def try_ntp(numeric_only=False):
    """Set the clock. Blocking -- nothing else runs while this is in here.

    The hostname fallback is the expensive one: ntptime.timeout bounds the NTP
    exchange but not getaddrinfo(), and a dead DNS server can hang the lookup
    for many minutes. That is what put ~20 minute holes in the log around each
    daily re-sync. So the periodic re-sync asks for numeric hosts only, which
    bounds it to three 8 second timeouts; the boot sync, where getting a real
    clock matters more than latency, still gets the name as a last resort.
    """
    ntptime.timeout = 8
    hosts = NTP_HOSTS[:-1] if numeric_only else NTP_HOSTS
    for host in hosts:
        ntptime.host = host
        try:
            ntptime.settime()
            print("ntp: synced via", host)
            return True
        except Exception as e:
            print("ntp fail via", host, repr(e))
    return False


def initial_sync(srv):
    # Try hard at boot, with backoff, BEFORE logging. We refuse to write
    # real-timestamped rows until the clock is genuinely set, so a reboot that
    # comes up before the network is ready can't silently log year-2000 times.
    # The backoff pumps the listener, so the existing log stays downloadable
    # throughout -- which is exactly when you most want to pull it off.
    delay = 1
    for _ in range(6):
        if try_ntp():
            print("ntp: clock set")
            return True
        sleep_pumping(srv, delay)
        delay = min(delay * 2, 15)
    print("ntp: not synced yet -- logging PAUSED, will keep retrying")
    return False


def unix_now():
    return time.time() + UNIX_OFFSET


def ensure_header():
    try:
        with open(LOG_PATH, "r"):
            pass
    except OSError:
        with open(LOG_PATH, "w") as f:
            f.write("unix_time,moisture,temp_c\n")


def log_event(kind, detail):
    """Append a '#' comment row: boot, IP change, tick-rate measurement.

    Best-effort and never fatal -- an event marker is worth less than the next
    reading. Before the first sync the timestamp on these rows is boot-relative
    (year 2000); that is only ever a marker's own label, never a data row's.
    """
    try:
        with open(LOG_PATH, "a") as f:
            f.write("#%s,%d,%s\n" % (kind, unix_now(), detail))
    except OSError:
        pass


def append_reading(t, m, c):
    with open(LOG_PATH, "a") as f:
        f.write("%d,%s,%s\n" % (t,
                                "" if m is None else str(m),
                                "" if c is None else "%.2f" % c))


def serve(cl):
    # Stream the whole log. We can't trust sendall() here: on this MicroPython/
    # lwIP build a non-blocking or partially-flushed socket makes it stop after
    # roughly one output buffer (~1 KB), which silently truncates the download.
    # So we put the socket in blocking mode and loop on write(), honoring the
    # returned byte count (a short write means "sent this many, call again"),
    # with a tiny pause so lwIP can drain its buffer between chunks.
    try:
        cl.settimeout(10)                 # blocking, but not forever
        with open(LOG_PATH, "rb") as f:
            while True:
                chunk = f.read(256)
                if not chunk:
                    break
                view = memoryview(chunk)
                sent = 0
                while sent < len(chunk):
                    n = cl.write(view[sent:])
                    if n is None:         # nothing accepted right now; let it drain
                        time.sleep_ms(20)
                        continue
                    sent += n
                    if sent < len(chunk):
                        time.sleep_ms(5)
    except OSError:
        pass
    finally:
        cl.close()


def pump(srv):
    """Serve one pending download if a client is waiting, else return at once."""
    if srv is None:
        return
    try:
        cl, _addr = srv.accept()
    except OSError:
        return
    serve(cl)


def sleep_pumping(srv, seconds):
    """time.sleep() that keeps answering downloads while it waits."""
    end = time.ticks_add(time.ticks_ms(), ticks_for(seconds))
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        pump(srv)
        time.sleep_ms(200)


def listen():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", TCP_PORT))
    srv.listen(1)
    srv.settimeout(0)             # non-blocking accept
    print("serving log on TCP port", TCP_PORT)
    return srv


def main():
    wlan = wifi_connect()
    ensure_header()

    # Bind before syncing the clock, not after. initial_sync() can spend
    # minutes failing, and with the socket opened afterwards every download in
    # that window got connection-refused -- indistinguishable, from the far
    # end, from a board that had stopped logging altogether.
    srv = listen()
    ip = wlan.ifconfig()[0] if wlan is not None and wlan.isconnected() else None
    log_event("boot", ip if ip else "no-ip")

    synced = initial_sync(srv)
    if synced:
        note_sync_anchor()

    now = time.ticks_ms()
    last_sample = time.ticks_add(now, -ticks_for(SAMPLE_INTERVAL_S))
    last_ntp = now                                 # baseline for re-sync
    last_retry = now
    last_net = now

    while True:
        now = time.ticks_ms()

        # Not synced yet: keep retrying (rate-limited). Logging stays paused so
        # we never emit a wrong (year-2000) timestamp.
        if not synced and time.ticks_diff(now, last_retry) >= ticks_for(NTP_RETRY_S):
            last_retry = now
            if try_ntp():
                synced = True
                last_ntp = now
                last_sample = time.ticks_add(time.ticks_ms(),
                                             -ticks_for(SAMPLE_INTERVAL_S))
                note_sync_anchor()
                print("ntp: clock set (logging resumed)")

        # Synced: re-sync periodically so a slow drift or a mid-run glitch
        # heals, and so we collect a second anchor to measure ticks against.
        # Hourly until that measurement lands, daily thereafter.
        if synced:
            due = NTP_CAL_S if not calibrated else NTP_RESYNC_S
            if time.ticks_diff(now, last_ntp) >= ticks_for(due):
                if try_ntp(numeric_only=True):
                    note_sync_anchor()
                last_ntp = time.ticks_ms()   # re-read: try_ntp() burned time

        # Keep the link up and notice when the address moves.
        if time.ticks_diff(now, last_net) >= ticks_for(NET_CHECK_S):
            last_net = now
            ip = net_check(wlan, ip)

        # Sample only when we trust the clock.
        if synced and time.ticks_diff(now, last_sample) >= ticks_for(SAMPLE_INTERVAL_S):
            m, c, spread = read_sample()
            append_reading(unix_now(), m, c)
            print("logged", m, c, "spread", spread)
            last_sample = now

        # Serve downloads regardless, so you can always pull whatever exists.
        pump(srv)

        time.sleep(0.2)


if __name__ == "__main__":
    main()

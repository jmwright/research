"""
main.py -- NodeMCU Amica (ESP8266 / MicroPython) soil logger + TCP server.

Every SAMPLE_INTERVAL_S it reads the Adafruit STEMMA (Seesaw) soil sensor's
moisture and temperature, appends a timestamped line to a CSV file on flash,
and serves the whole file to any client that connects on TCP_PORT. A tiny
Python client (client/download.py) pulls it to your laptop.

Time: we sync the clock over NTP once at boot so timestamps are real Unix
seconds (UTC), which is what lets the visualizer compute time-of-day / phase.
MicroPython counts seconds from 2000-01-01, so we add the 1970->2000 offset to
emit standard Unix timestamps.

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
NTP_RETRY_S = 30              # while unsynced, retry the clock this often
NTP_RESYNC_S = 24 * 3600      # re-sync the clock once a day
TCP_PORT = getattr(config, "TCP_PORT", 8266)
LOG_PATH = "soil_log.csv"
UNIX_OFFSET = 946684800       # seconds between 1970-01-01 and 2000-01-01

i2c = machine.SoftI2C(scl=machine.Pin(I2C_SCL), sda=machine.Pin(I2C_SDA))


# ---- Seesaw reads ----
def read_moisture():
    try:
        i2c.writeto(SEESAW_ADDR, bytes([0x0F, 0x10]))   # TOUCH base, channel 0
        time.sleep_ms(5)
        d = i2c.readfrom(SEESAW_ADDR, 2)
        v = (d[0] << 8) | d[1]
        return None if v > 4095 else v
    except OSError:
        return None


def read_temp_c():
    try:
        i2c.writeto(SEESAW_ADDR, bytes([0x00, 0x04]))   # STATUS base, TEMP
        time.sleep_ms(5)
        d = i2c.readfrom(SEESAW_ADDR, 4)
        raw = (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]
        return raw / 65536.0
    except OSError:
        return None


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


# Public NTP servers as raw IPs first (work even when DNS is broken), then a
# hostname as a last resort for networks that do resolve. The -2 error you can
# hit is a DNS failure -- some access points/hotspots hand out no working DNS
# server, which kills name lookup while numeric-IP traffic still works.
NTP_HOSTS = ("216.239.35.0",    # time.google.com  (confirmed working here)
             "129.6.15.28",     # time.nist.gov
             "162.159.200.1",   # time.cloudflare.com
             "pool.ntp.org")    # name fallback (needs working DNS)


def try_ntp():
    ntptime.timeout = 8
    for host in NTP_HOSTS:
        ntptime.host = host
        try:
            ntptime.settime()
            print("ntp: synced via", host)
            return True
        except Exception as e:
            print("ntp fail via", host, repr(e))
    return False


def initial_sync():
    # Try hard at boot, with backoff, BEFORE logging. We refuse to write
    # real-timestamped rows until the clock is genuinely set, so a reboot that
    # comes up before the network is ready can't silently log year-2000 times.
    delay = 1
    for _ in range(6):
        if try_ntp():
            print("ntp: clock set")
            return True
        time.sleep(delay)
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


def main():
    wifi_connect()
    ensure_header()
    synced = initial_sync()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", TCP_PORT))
    srv.listen(1)
    srv.settimeout(0)             # non-blocking accept
    print("serving log on TCP port", TCP_PORT)

    now = time.ticks_ms()
    last_sample = now - SAMPLE_INTERVAL_S * 1000   # sample immediately once synced
    last_ntp = now                                 # baseline for daily re-sync
    last_retry = now

    while True:
        now = time.ticks_ms()

        # Not synced yet: keep retrying (rate-limited). Logging stays paused so
        # we never emit a wrong (year-2000) timestamp.
        if not synced and time.ticks_diff(now, last_retry) >= NTP_RETRY_S * 1000:
            last_retry = now
            if try_ntp():
                synced = True
                last_ntp = now
                last_sample = now - SAMPLE_INTERVAL_S * 1000
                print("ntp: clock set (logging resumed)")

        # Synced: re-sync once a day so a slow drift or a mid-run glitch heals.
        # Best-effort -- on failure we keep the current time and try again later.
        if synced and time.ticks_diff(now, last_ntp) >= NTP_RESYNC_S * 1000:
            if try_ntp():
                print("ntp: daily re-sync ok")
            last_ntp = now           # reset either way so we don't hammer

        # Sample only when we trust the clock.
        if synced and time.ticks_diff(now, last_sample) >= SAMPLE_INTERVAL_S * 1000:
            m = read_moisture()
            c = read_temp_c()
            append_reading(unix_now(), m, c)
            print("logged", m, c)
            last_sample = now

        # Serve downloads regardless, so you can always pull whatever exists.
        try:
            cl, _addr = srv.accept()
            serve(cl)
        except OSError:
            pass

        time.sleep(0.2)


if __name__ == "__main__":
    main()

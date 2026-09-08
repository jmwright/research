"""Generate a synthetic soil_sample.csv so the downloader and visualizer can be
tested without hardware. Models a daily watering + drying rhythm (faster drying
midday), a slow drift (plant grows thirstier over days), a diurnal temperature
swing, sensor noise, and a couple of injected gaps. Columns match the device:

    unix_time,moisture,temp_c
"""
import math
import random

random.seed(3)

DAYS = 10
STEP_S = 15 * 60                      # 15-minute samples
START = 1_700_000_000                 # arbitrary Unix start (a Tuesday UTC)
rows = []

moisture = 1500.0
for i in range(DAYS * 24 * 4):
    t = START + i * STEP_S
    tod = (t % 86400) / 86400.0       # phase of day, 0..1 (UTC)
    day = i // (24 * 4)

    # watering pulse ~07:00 each day
    if abs(tod - 7 / 24.0) < (STEP_S / 86400.0) / 2:
        moisture = 1500.0 + random.uniform(-40, 40)

    # drying: base rate rises with day (growth); faster in the warm afternoon
    base_k = 0.030 + 0.0015 * day
    diurnal = 1.0 + 0.6 * math.sin(2 * math.pi * (tod - 0.30))   # peak ~afternoon
    k = base_k * max(0.2, diurnal)
    dt_h = STEP_S / 3600.0
    moisture = 300 + (moisture - 300) * math.exp(-k * dt_h)
    moisture += random.gauss(0, 1.5)

    temp = 22 + 4 * math.sin(2 * math.pi * (tod - 0.60)) + 0.1 * day + random.gauss(0, 0.3)

    # inject two sensor gaps to prove the viz handles them
    if (day == 3 and 0.4 < tod < 0.55) or (day == 7 and 0.1 < tod < 0.18):
        continue

    rows.append((t, moisture + random.gauss(0, 10), temp))

with open("soil_sample.csv", "w") as f:
    f.write("unix_time,moisture,temp_c\n")
    for t, m, c in rows:
        f.write("%d,%.1f,%.2f\n" % (t, max(0, m), c))

print("wrote soil_sample.csv with %d rows over %d days" % (len(rows), DAYS))

# Plant Tender

An experimental plant tending (watering) system designed to learn what a plant needs over time.

## Theory of Operation

The physical claim is that soil sheds water in proportion to how much is has above its dry asymptote.

```
dM/Dt = -k * (M - M_floor)
```

expanded:

```
DM/dt = -k * M + k * M_floor
```

This looks very much like `y = a * x + b` (equation of a line), where `a` = `-k` and `b` = `k * M_floor`. So:

* `k` = `-a`
* `M_floor` = `-b / a`

This system is solving for a straight line, with rate on the y-axis and moisture level on the x-axis. Two parameters, so no matrix bigger than 2x2.

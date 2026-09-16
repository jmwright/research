import cadquery as cq

def bar():
    bar = cq.Workplane().circle(40.0 / 2.0).extrude(104.0)

    return bar

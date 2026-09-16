import cadquery as cq

def i():
    i = cq.Workplane().circle(40.0 / 2.0).extrude(59.0)

    i = (i.faces(">Z")
          .workplane(offset=26)
          .circle(40.0 / 2.0)
          .extrude(19.0))

    return i

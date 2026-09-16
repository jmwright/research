import cadquery as cq

# The glass exists to be a near miss for the mug, so its body has to be the mug's
# body exactly - same diameter, same height, same wall, same dowel features. The
# only difference between the two objects is the handle, and that is the whole
# point: it makes recognition depend on resolving a part rather than on telling a
# cylinder from a cube. Importing the parameters rather than restating them is
# what keeps that true if the mug is ever resized.
try:
    from .mug import mug_diameter, mug_height, wall_thickness
except ImportError:  # loaded flat, the way CQ-editor and the STL export do it
    from mug import mug_diameter, mug_height, wall_thickness


def glass():
    """
    Generates the glass test object - the mug's body with no handle.
    """

    # Cylindrical body, identical to the mug's
    glass = cq.Workplane().circle(mug_diameter / 2.0).extrude(mug_height)
    glass = (glass.faces(">Z")
                  .workplane(centerOption="CenterOfBoundBox")
                  .circle(mug_diameter / 2.0 - wall_thickness * 2.0)
                  .cutBlind(-mug_height + 6.0))

    # Boss and hole for dowel rod
    glass = (glass.faces(">Z[-2]")
                  .workplane(centerOption="CenterOfBoundBox")
                  .circle(12.0 / 2.0)
                  .extrude(20.0))
    glass = (glass.faces("<Z")
                  .workplane(centerOption="CenterOfBoundBox")
                  .circle(8.0 / 2.0)
                  .cutBlind(-24.0))

    # Add arounds
    glass = (glass.faces(">Z").fillet(4.0))
    glass = (glass.faces("<Z").edges(cq.selectors.RadiusNthSelector(1)).fillet(4.0))

    return glass

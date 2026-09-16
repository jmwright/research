import cadquery as cq

def block():
    # Main shape of the block
    block = cq.Workplane().box(50.0, 50.0, 50.0)

    # Axis tags
    block = (block.faces(">X")
                  .workplane(centerOption="CenterOfBoundBox")
                  .text("+X", fontsize=25.0, distance=-5.0))
    block = (block.faces(">Y")
                  .workplane(centerOption="CenterOfBoundBox")
                  .text("+Y", fontsize=25.0, distance=-5.0))
    block = (block.faces(">Z")
                  .workplane(centerOption="CenterOfBoundBox")
                  .text("+Z", fontsize=25.0, distance=-5.0))
    block = (block.faces("<X")
                  .workplane(centerOption="CenterOfBoundBox")
                  .text("-X", fontsize=25.0, distance=-5.0))
    block = (block.faces("<Y")
                  .workplane(centerOption="CenterOfBoundBox")
                  .text("-Y", fontsize=25.0, distance=-5.0))
    block = (block.faces("<Z")
                  .workplane(centerOption="CenterOfBoundBox")
                  .hole(8.0, depth=24.0))

    return block

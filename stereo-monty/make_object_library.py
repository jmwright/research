#!/usr/bin/env python3
"""
Builds the Habitat object library Monty pretrains on, from the printed parts' STLs.

Recognition needs alternatives, so the library holds the mug and the 50 mm block -
the two objects that were actually printed, exported from the same geometry the
printer was given. That is the point of doing it this way rather than scanning:
the graph Monty learns is ground truth, not a reconstruction.

Three things here are not obvious.

**Both objects get the identical colour.** Monty's evidence calculator weights hue
at 2.0, the heaviest feature it has, so two objects in different colours would let
the LM win on colour and say nothing about shape. Giving them one colour makes the
hsv term cancel between hypotheses and turns the comparison into the pure
shape/curvature test it is meant to be. The value is the mint the rig actually
observed on the mug, in linear space, because Monty compares the graph's stored hsv
against the observed hsv and they have to line up.

**The mesh stays in millimetres and the object config scales it.** `outputUnit` does
not scale a GLTF - the accessor min/max come out in whatever unit the source was -
so units are handled once, in `scale` in the object config, where they are visible.

**Vertex normals are cosmetic here.** Monty's surface agent sees Habitat's depth
buffer, not the mesh normals, so welding the STL's duplicated vertices and averaging
face normals changes shading and nothing else. It is done because a faceted mug is
hard to look at when `show_sensor_output` is on.

The Habitat loader wants a flat directory holding `<name>.glb` beside
`<name>.object_config.json`. It looks for a `configs/` or `objects/` subdirectory
first and only treats `data_path` itself as the config directory when there is
neither, so do not add one.

Usage:

    ./make_object_library.py <stl dir> <output dir>

<stl dir> holds mug.stl and block.stl. <output dir> is what the pretraining config's
environment.env_init_args.data_path points at, conventionally
~/tbp/data/rig_objects.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

# The mint the rig read off the mug on 2026-08-24 - RGB (158, 231, 187) as sRGB,
# converted to the linear values a glTF baseColorFactor is defined in. Both objects
# carry it, so hue cancels between hypotheses; see the module docstring.
BASE_COLOR = (0.342, 0.799, 0.496, 1.0)
ROUGHNESS = 0.8  # printed PETG is glossy, but a mirror finish renders as noise

# The CAD extrudes +Z and the mug's handle runs +X, so the object's own up axis is Z
# and its front is +Y. Habitat needs telling; it assumes neither.
UP = [0, 0, 1]
FRONT = [0, 1, 0]
MILLIMETRES = [0.001, 0.001, 0.001]

# Named explicitly rather than globbed, because the STLs are exported to a
# download directory that holds a great deal else
OBJECTS = {"rig_mug": "mug.stl", "rig_block": "block.stl", "rig_glass": "glass.stl"}

FLOAT, UNSIGNED_INT = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942


def read_stl(path):
    """Reads a binary STL, returning its triangles as an (n, 3, 3) array."""

    raw = path.read_bytes()

    if raw[:5].lower() == b"solid" and b"facet" in raw[:512]:
        raise ValueError(f"{path} is an ASCII STL; this reads binary ones")

    count = struct.unpack("<I", raw[80:84])[0]
    expected = 84 + 50 * count

    # A truncated STL still parses if the count is trusted blindly, and the
    # missing triangles come back as whatever followed in memory
    if len(raw) != expected:
        raise ValueError(f"{path} claims {count} triangles, so it should be "
                         f"{expected} bytes, but it is {len(raw)}")

    records = np.frombuffer(raw[84:expected], dtype=np.uint8).reshape(count, 50)

    # Each 50-byte record is a normal and three vertices, then two bytes of
    # attributes that break the alignment and have to be dropped before the view
    vectors = records[:, :48].copy().view(np.float32).reshape(count, 4, 3)

    return vectors[:, 1:, :]


def weld(triangles):
    """Merges the STL's duplicated vertices and averages normals across the seams."""

    vertices, indices = np.unique(triangles.reshape(-1, 3), axis=0,
                                  return_inverse=True)
    faces = indices.reshape(-1, 3).astype(np.uint32)

    corners = vertices[faces]
    # Not normalized, so its length is twice the triangle's area and larger
    # triangles pull the averaged normal further, which is what we want
    face_normals = np.cross(corners[:, 1] - corners[:, 0],
                            corners[:, 2] - corners[:, 0])

    normals = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)

    return vertices.astype(np.float32), faces, (normals / np.where(lengths, lengths, 1)).astype(np.float32)


def pad(data, fill=b"\0"):
    """Pads to the 4-byte boundary every glTF offset has to sit on."""

    return data + fill * (-len(data) % 4)


def write_glb(path, name, vertices, faces, normals):
    """Writes one mesh as a self-contained binary glTF."""

    blocks = [vertices.tobytes(), normals.tobytes(), faces.tobytes()]
    offsets, cursor = [], 0

    for block in blocks:
        offsets.append(cursor)
        cursor += len(pad(block))

    views = [
        {"buffer": 0, "byteOffset": offsets[0], "byteLength": len(blocks[0]),
         "target": ARRAY_BUFFER},
        {"buffer": 0, "byteOffset": offsets[1], "byteLength": len(blocks[1]),
         "target": ARRAY_BUFFER},
        {"buffer": 0, "byteOffset": offsets[2], "byteLength": len(blocks[2]),
         "target": ELEMENT_ARRAY_BUFFER},
    ]

    gltf = {
        "asset": {"version": "2.0", "generator": "make_object_library.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1},
            "indices": 2,
            "material": 0,
        }]}],
        "materials": [{
            "name": "printed_plastic",
            "pbrMetallicRoughness": {
                "baseColorFactor": list(BASE_COLOR),
                "metallicFactor": 0.0,
                "roughnessFactor": ROUGHNESS,
            },
        }],
        "buffers": [{"byteLength": cursor}],
        "bufferViews": views,
        "accessors": [
            # POSITION is the one accessor glTF requires bounds on
            {"bufferView": 0, "componentType": FLOAT, "count": len(vertices),
             "type": "VEC3", "min": vertices.min(axis=0).tolist(),
             "max": vertices.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": FLOAT, "count": len(normals),
             "type": "VEC3"},
            {"bufferView": 2, "componentType": UNSIGNED_INT, "count": faces.size,
             "type": "SCALAR"},
        ],
    }

    # The JSON chunk pads with spaces rather than nulls; the binary one with nulls
    json_chunk = pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    bin_chunk = b"".join(pad(block) for block in blocks)

    body = (struct.pack("<II", len(json_chunk), JSON_CHUNK) + json_chunk +
            struct.pack("<II", len(bin_chunk), BIN_CHUNK) + bin_chunk)

    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def write_object_config(path, name):
    """Writes the sidecar Habitat reads to find and place the mesh."""

    path.write_text(json.dumps({
        "render_asset": f"{name}.glb",
        "requires_lighting": True,
        "scale": MILLIMETRES,
        "up": UP,
        "front": FRONT,
    }, indent=2) + "\n")


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 1

    source, destination = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    for name, filename in OBJECTS.items():
        stl = source / filename

        if not stl.exists():
            print(f" No {filename} in {source}")
            return 1

        vertices, faces, normals = weld(read_stl(stl))
        write_glb(destination / f"{name}.glb", name, vertices, faces, normals)
        write_object_config(destination / f"{name}.object_config.json", name)

        extent = (vertices.max(axis=0) - vertices.min(axis=0)) * MILLIMETRES[0]
        print(f" {name:10} {len(faces):7d} triangles, {len(vertices):7d} vertices, "
              f"{extent[0]:.3f} x {extent[1]:.3f} x {extent[2]:.3f} m")

    print(f" wrote {destination}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

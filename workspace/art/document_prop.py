"""An open project folder with a turning sheet, in the portrait rig's world space."""

import math

import bpy


def make_folder(scene, start):
    materials = []
    for name, rgb in (
        ("Blue cover", (.055, .19, .29)), ("Tab", (.07, .43, .46)),
        ("Paper edges", (.59, .67, .68)), ("Ivory paper", (.96, .93, .83)),
        ("Ink", (.14, .25, .31)), ("Rule", (.43, .59, .61)),
    ):
        mat = bpy.data.materials.new("Documents | " + name)
        mat.diffuse_color = (*rgb, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = mat.diffuse_color
        shader.inputs["Roughness"].default_value = .88
        materials.append(mat)

    def page(name, side, turning=False):
        coords, faces, inks = [], [], []

        def strip(x0, x1, y0, y1, depth, ink, divisions=1):
            first = len(coords)
            for i in range(divisions + 1):
                u = x0 + (x1 - x0) * i / divisions
                coords.extend(((u, y0, depth), (u, y1, depth)))
                if i:
                    k = first + 2 * (i - 1)
                    faces.append((k, k + 2, k + 3, k + 1))
                    inks.append(ink)

        if not turning:
            strip(0, 1.06, -.045, 1.035, .012, 0, 16)
            if side == 1:
                strip(.69, .96, 1.01, 1.105, .012, 1)
            strip(.008, 1.023, -.018, 1.012, .005, 2, 16)
        strip(.008, 1, 0, 1, 0, 3, 16)
        strip(.13, .73, .803, .855, -.002, 4, 8)
        strip(.13, .87, .743, .755, -.002, 1, 8)
        for row, width in enumerate((.66, .56, .69, .40)):
            y = .63 - row * .099
            strip(.14, .14 + width, y, y + .023, -.002, 5, 8)
        strip(.62, .84, .105, .165, -.002, 1, 4)

        def position(item, phase):
            u, v, offset = item
            angle = .23 + (math.pi - .46) * phase
            curl = .39 * math.sin(math.pi * phase) * u
            a = angle + curl
            # Lift the turning page toward the camera, with a slight diagonal
            # in the fold. Ink and paper share the same deforming surface.
            return (.014 - .018 * v + .273 * u * math.cos(a),
                    -.332 + .095 * v - .27 * u * math.sin(a) + offset - (.012 if turning else 0),
                    .676 + .35 * v + .026 * u + .032 * u * math.sin(a) + .019 * u * math.cos(a))

        initial = 0 if side == 1 else 1
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([position(p, initial) for p in coords], [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        for mat in materials:
            mesh.materials.append(mat)
        for face, index in zip(mesh.polygons, inks):
            face.material_index = index
        for prop in ("hide_render", "hide_viewport"):
            for frame, hidden in ((1, True), (start - 1, True), (start, False)):
                setattr(obj, prop, hidden)
                obj.keyframe_insert(data_path=prop, frame=frame)
        if turning:
            obj.shape_key_add(name="Right page")
            keys = mesh.shape_keys
            keys.use_relative = False
            for step in range(1, 13):
                key = obj.shape_key_add(name=f"Turn {step:02d}", from_mix=False)
                key.interpolation = "KEY_LINEAR"
                for vertex, p in zip(key.data, coords):
                    vertex.co = position(p, step / 12)
            return keys

    page("Documents | left stack", -1)
    page("Documents | right stack", 1)
    return page("Documents | turning sheet", 1, turning=True)


def turn_progress(t):
    # Read each spread briefly, turn the sheet, then compare the first page.
    position = min(1, max(0, (t - .15) / .34)) - min(1, max(0, (t - .63) / .28))
    return position * position * (3 - 2 * position)

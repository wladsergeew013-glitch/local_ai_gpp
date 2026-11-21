"""Build the rigged Workspace companion from the approved face and its turnaround.

blender -b -P build_companion.py -- OUTPUT [--preview] [--render] [--state documents]
The blend file packs all four reference images. The default camera matches the desktop view.
"""

import math
import sys
from functools import lru_cache
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


ART = Path(__file__).resolve().parent
sys.path.insert(0, str(ART))
from document_prop import make_folder, turn_progress

args = sys.argv[sys.argv.index("--") + 1:]
out = Path(args[0]).resolve()
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def texture(filename, label):
    image = bpy.data.images.load(str(ART / filename))
    image.name = label
    image.pack()
    width, height = image.size
    data = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(data)
    return image, data.reshape(height, width, 4)[::-1]


portrait, face_pixels = texture("face-02.png", "Approved face 02 | original")
atlas, body_pixels = texture("turnaround-02.png", "Blue bomber | front and back")
profile, profile_pixels = texture("profile-02.png", "Head 02 | lateral study")
oblique, oblique_pixels = texture("bomber-original.png", "Original bomber | temple hair study")
assert tuple(portrait.size) == (1254, 1254) and tuple(atlas.size) == (1536, 1024)
silhouette = body_pixels[:, :, 3] > .64
# Blender exposes byte texture RGB in linear light; detect the outline in sRGB.
f = face_pixels[:, :, :3]
rgb = np.where(f < .0031308, 12.92 * f, 1.055 * np.maximum(f, 0) ** (1 / 2.4) - .055)
foreground = (np.max(rgb, axis=2) < .75) | ((rgb[:, :, 0] - rgb[:, :, 2]) > .21)
skin = (rgb[:, :, 0] > .55) & (rgb[:, :, 0]-rgb[:, :, 2] > .20) & (rgb[:, :, 0]-rgb[:, :, 1] > .075)


def interior_lookup(mask):
    valid=mask.copy()
    for offset in (-2,2):
        valid &= np.roll(mask,offset,axis=1) & np.roll(mask,offset,axis=0)
    cols=np.arange(mask.shape[1])[None,:]
    left=np.maximum.accumulate(np.where(valid,cols,-10000),axis=1)
    right=np.minimum.accumulate(np.where(valid,cols,10000)[:,::-1],axis=1)[:,::-1]
    nearest=np.where(cols-left<right-cols,left,right)
    return np.where((nearest>=0)&(nearest<mask.shape[1]),nearest,cols)


face_samples=interior_lookup(foreground)
side_samples=interior_lookup(profile_pixels[:,:,3]>.80)
def hair_mask(pixels):
    return ((pixels[:,:,2] > pixels[:,:,0]*1.05) &
            (pixels[:,:,2] >= pixels[:,:,1]*.98) & (pixels[:,:,3] > .72))


side_hair=interior_lookup(hair_mask(profile_pixels))
rear_hair=hair_mask(body_pixels)
temple_mask=hair_mask(oblique_pixels)
temple_mask[:,:850]=False
temple_mask[:,1440:]=False
temple_hair=interior_lookup(temple_mask)
CX, REAR = 419.5, 1117
UNIT = .002
FACE_X, FACE_Y = .48, .475


def face_x(x):
    return CX + (x - 627.5) * FACE_X


def face_row(y):
    return 14 + (y - 22) * FACE_Y


def point(x, row, depth=0):
    return Vector(((x - CX) * UNIT, depth * UNIT, (1012 - row) * UNIT))


def lerp_table(y, knots):
    return float(np.interp(y, [p[0] for p in knots], [p[1] for p in knots]))


def uv(image, x, y):
    return ((x + .5) / image.size[0], 1 - (y + .5) / image.size[1])


def material(name, front, roughness, lateral=False):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    sampled = []
    for slot, img in (("Front", front), ("Back", atlas)):
        coords = nodes.new("ShaderNodeUVMap")
        coords.uv_map = slot
        sampler = nodes.new("ShaderNodeTexImage")
        sampler.image = img
        sampler.extension = "EXTEND"
        links.new(coords.outputs["UV"], sampler.inputs["Vector"])
        sampled.append(sampler.outputs["Color"])
    weights = nodes.new("ShaderNodeAttribute")
    weights.attribute_name = "Back weight"
    mix = nodes.new("ShaderNodeMixRGB")
    links.new(weights.outputs["Fac"], mix.inputs[0])
    for index, color in enumerate(sampled, 1):
        links.new(color, mix.inputs[index])
    color_output=mix.outputs[0]
    if lateral:
        coords=nodes.new("ShaderNodeUVMap")
        coords.uv_map="Side"
        sampler=nodes.new("ShaderNodeTexImage")
        sampler.image=profile
        links.new(coords.outputs["UV"],sampler.inputs["Vector"])
        side=nodes.new("ShaderNodeAttribute")
        side.attribute_name="Side weight"
        geom=nodes.new("ShaderNodeNewGeometry")
        local=nodes.new("ShaderNodeVectorTransform")
        local.vector_type="VECTOR"
        local.convert_from,local.convert_to="WORLD","OBJECT"
        links.new(geom.outputs["Incoming"],local.inputs["Vector"])
        axes=nodes.new("ShaderNodeSeparateXYZ")
        links.new(local.outputs["Vector"],axes.inputs[0])
        abs_x=nodes.new("ShaderNodeMath")
        abs_x.operation="ABSOLUTE"
        links.new(axes.outputs["X"],abs_x.inputs[0])
        angle=nodes.new("ShaderNodeMapRange")
        angle.inputs["From Min"].default_value=.06
        angle.inputs["From Max"].default_value=.68
        links.new(abs_x.outputs[0],angle.inputs["Value"])
        weight=nodes.new("ShaderNodeMath")
        weight.operation="MULTIPLY"
        links.new(angle.outputs[0],weight.inputs[0])
        links.new(side.outputs["Fac"],weight.inputs[1])
        cross=nodes.new("ShaderNodeMixRGB")
        links.new(weight.outputs[0],cross.inputs[0])
        links.new(color_output,cross.inputs[1])
        links.new(sampler.outputs["Color"],cross.inputs[2])
        color_output=cross.outputs[0]
        coords=nodes.new("ShaderNodeUVMap")
        coords.uv_map="Temple"
        curl=nodes.new("ShaderNodeTexImage")
        curl.image=oblique
        links.new(coords.outputs["UV"],curl.inputs["Vector"])
        temple=nodes.new("ShaderNodeAttribute")
        temple.attribute_name="Temple weight"
        # The source's three-quarter hair study fills the temple only on turns.
        window=nodes.new("ShaderNodeValToRGB")
        ramp=window.color_ramp
        ramp.elements[0].color=(0,0,0,1)
        ramp.elements[1].color=(0,0,0,1)
        for at,value in ((.12,0),(.38,1),(.72,1),(.97,0)):
            ramp.elements.new(at).color=(value,value,value,1)
        links.new(abs_x.outputs[0],window.inputs[0])
        amount=nodes.new("ShaderNodeMath")
        amount.operation="MULTIPLY"
        links.new(window.outputs[0],amount.inputs[0])
        links.new(temple.outputs["Fac"],amount.inputs[1])
        curls=nodes.new("ShaderNodeMixRGB")
        links.new(amount.outputs[0],curls.inputs[0])
        links.new(color_output,curls.inputs[1])
        links.new(curl.outputs["Color"],curls.inputs[2])
        color_output=curls.outputs[0]
    diffuse = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(color_output, diffuse.inputs["Base Color"])
    diffuse.inputs["Roughness"].default_value = roughness
    diffuse.inputs["Specular IOR Level"].default_value = .15
    emission = nodes.new("ShaderNodeEmission")
    links.new(color_output, emission.inputs["Color"])
    # Keep the approved studio colours while allowing depth and moving light on turns.
    shader = nodes.new("ShaderNodeMixShader")
    shader.inputs[0].default_value = .035 if lateral else .20
    links.new(emission.outputs[0], shader.inputs[1])
    links.new(diffuse.outputs[0], shader.inputs[2])
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(shader.outputs[0], output.inputs["Surface"])
    return mat


head_mat = material("Face 02 | skin and sculpted hair", portrait, .63, lateral=True)
cloth_mat = material("Woven bomber, T-shirt and cuffs", atlas, .85)
pants_mat = material("Navy twill and suede", atlas, .78)
skin_mat = material("Neck", atlas, .6)
rigdata = bpy.data.armatures.new("Mini assistant skeleton")
rig = bpy.data.objects.new("Workspace helper | pose controls", rigdata)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
joints = {}


def joint(name, pos, parent=None):
    b = rigdata.edit_bones.new(name)
    b.head, b.tail = pos, pos + Vector((0, .065, 0))
    if parent:
        b.parent = rigdata.edit_bones[parent]
    joints[name] = pos


joint("Pelvis", point(CX, 716, 5))
joint("Chest", point(CX, 598, 4), "Pelvis")
joint("Neck", point(CX, 463, 1), "Chest")
joint("Head", point(CX, 428, 4), "Neck")
for s, tag in ((-1, "L"), (1, "R")):
    joint("UpperArm." + tag, point(CX + s*96, 497, 0), "Chest")
    joint("Forearm." + tag, point(CX + s*174, 596, 2), "UpperArm." + tag)
    joint("Hand." + tag, point(CX + s*237, 681, -4), "Forearm." + tag)
    joint("Thigh." + tag, point(CX + s*57, 731, 5), "Pelvis")
    joint("Shin." + tag, point(CX + s*66, 845, 5), "Thigh." + tag)
    joint("Foot." + tag, point(CX + s*78, 950, 0), "Shin." + tag)
bpy.ops.object.mode_set(mode="OBJECT")
rigdata.display_type = "STICK"


def smooth(t):
    t = min(1, max(0, t))
    return t*t*(3-2*t)


def blend_bones(y, start, end, first, second):
    t = smooth((y - start) / (end - start))
    return ((first, 1-t), (second, t))


def mesh_object(name, vertices, faces, shading, fronts, backs, back_weights, bindings,
                sides=(), side_weights=(), temples=(), temple_weights=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    mesh.materials.append(shading)
    mesh.polygons.foreach_set("use_smooth", np.ones(len(mesh.polygons), dtype=bool))
    if fronts:
        indices = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get("vertex_index", indices)
        for layer_name, values in (("Front", fronts), ("Back", backs), ("Side", sides), ("Temple",temples)):
            if not values:
                continue
            layer = mesh.uv_layers.new(name=layer_name)
            layer.data.foreach_set("uv", np.asarray(values, np.float32)[indices].ravel())
        attr = mesh.attributes.new("Back weight", "FLOAT", "POINT")
        attr.data.foreach_set("value", np.asarray(back_weights, np.float32))
        if sides:
            attr=mesh.attributes.new("Side weight","FLOAT","POINT")
            attr.data.foreach_set("value",np.asarray(side_weights,np.float32))
            attr=mesh.attributes.new("Temple weight","FLOAT","POINT")
            attr.data.foreach_set("value",np.asarray(temple_weights,np.float32))
    groups = {}
    for i, binding in enumerate(bindings):
        for bone, weight in binding:
            if weight <= 0:
                continue
            if bone not in groups:
                groups[bone] = obj.vertex_groups.new(name=bone)
            groups[bone].add([i], weight, "REPLACE")
    obj.parent = rig
    arm = obj.modifiers.new("Pose | preserve joint volume", "ARMATURE")
    arm.object, arm.use_deform_preserve_volume = rig, True
    return obj


def rings(name, rows, segments, section, surface, mat):
    vertices, faces, front_uvs, rear_uvs, reverse, weights, marks = [], [], [], [], [], [], []
    side_uvs,side_weights,temples,temple_weights=[],[],[],[]
    for y in rows:
        bounds = section(y)
        if bounds is None:
            continue
        left, right, rear_left, rear_right = bounds
        if right - left < .4:
            continue
        center, radius = (left+right)/2, (right-left)/2
        rear_center, rear_radius = (rear_left+rear_right)/2, (rear_right-rear_left)/2
        base = len(vertices)
        for j in range(segments):
            angle = math.tau*j/segments
            sine, cosine = math.sin(angle), math.cos(angle)
            x = center + radius*sine
            inset = min(1.6, radius*.12)
            front_sample = center + max(0, radius-inset)*sine
            rear_sample = rear_center - max(0, rear_radius-min(2,rear_radius*.15))*sine
            position, front, rear, binding, mark = surface(x,y,radius,cosine,front_sample,rear_sample)
            vertices.append(position)
            front_uvs.append(front)
            rear_uvs.append(rear)
            weights.append(binding)
            marks.append(mark)
            reverse.append(smooth(.5 - cosine/(.34 if mat == head_mat else .70)))
            if mat == head_mat:
                row,_,_=side_section(y)
                px=round(650 + position.y/(UNIT*.43))
                px=min(1253,max(0,px))
                scalp=(mark is not None and
                       (cosine<0 or (y<700 and
                        (y<382 or abs(x-627.5)>192) and
                        rgb[y,min(1253,max(0,round(front_sample))),2] >
                        rgb[y,min(1253,max(0,round(front_sample))),0]*1.04)))
                px=side_hair[row,px] if scalp else side_samples[row,px]
                side_uvs.append(uv(profile,px,row))
                side_weights.append(1-smooth((abs(cosine)-.18)/.49))
                col=round(1163+(abs(position.x)*.848+position.y*.53)/UNIT)
                oy=max(24,min(445,round(face_row(y)*1.01+8)))
                ox=temple_hair[oy,min(1535,max(850,col))]
                temples.append(uv(oblique,ox,oy))
                threshold=lerp_table(y,[(22,80),(365,153),(490,194),(574,231),(690,260)])
                coverage=smooth((abs(x-627.5)-threshold)/35)*smooth((723-y)/39)
                coverage*=1-smooth((abs(cosine)-.55)/.26)
                temple_weights.append(coverage if mark is not None else 0)
            if base:
                k = (j + 1) % segments
                faces.append((base-segments+j, base+j, base+k, base-segments+k))
    if len(vertices) < segments*2:
        raise ValueError("Empty component: " + name)
    faces.extend((tuple(range(segments)), tuple(reversed(range(len(vertices)-segments, len(vertices))))))
    return mesh_object(name, vertices, faces, mat, front_uvs, rear_uvs, reverse, weights,
                       side_uvs,side_weights,temples,temple_weights), marks


def alpha_span(y, lo, hi):
    y = max(0, min(silhouette.shape[0]-1, round(y)))
    start, stop = max(0, math.ceil(lo)), min(silhouette.shape[1], math.floor(hi)+1)
    xs = np.flatnonzero(silhouette[y, start:stop]) + start
    return (float(xs[0]), float(xs[-1])) if len(xs) else None


def gaussian(x, y, cx, cy, rx, ry):
    return math.exp(-((x-cx)/rx)**2 - ((y-cy)/ry)**2)


@lru_cache(maxsize=1024)
def side_section(y):
    row=round(lerp_table(y,[(22,43),(112,131),(345,417),(483,564),(611,709),
                           (735,832),(819,923),(900,1010),(937,1051)]))
    # Smooth the volume at the forehead and nape, where overhanging curls
    # produce small gaps between otherwise continuous contour samples.
    fore=lerp_table(row,[(43,580),(60,505),(121,367),(160,359),(200,284),
                        (300,195),(417,164),(455,173),(493,199),(526,257),
                        (600,256),(709,267),(750,263),(780,247),(820,194),
                        (837,192),(850,197),(870,220),(900,235),(940,244),
                        (975,258),(1010,268),(1025,276),(1040,292),(1051,317)])
    aft=lerp_table(row,[(43,646),(60,731),(121,844),(200,979),(300,1064),
                       (455,1128),(500,1135),(600,1120),(710,1087),(780,1046),
                       (834,1009),(900,943),(940,894),(975,840),(1010,756),
                       (1030,725),(1051,721)])
    return row,(fore-650)*.43,(aft-650)*.43


def head_contour(y):
    if y < 588:
        xs = np.flatnonzero(foreground[y, 183:1064]) + 183
        if not len(xs):
            return None
        return float(xs[0]), float(xs[-1])
    if y < 778:
        # Ears are separate protruding volumes. These are the temple and jaw behind them.
        left = lerp_table(y,[(588,277),(600,317),(625,331),(653,341),(690,365),
                             (701,375),(735,380),(757,385),(778,389)])
        right = lerp_table(y,[(588,982),(600,943),(625,928),(653,916),(690,892),
                              (701,882),(735,875),(757,871),(778,866)])
        return left, right
    if y < 906:
        xs = np.flatnonzero((rgb[y, 300:950, 0] - rgb[y, 300:950, 2]) > .22) + 300
        if len(xs):
            return float(xs[0]),float(xs[-1])
    radius = lerp_table(y,[(905,112),(912,95),(918,78),(925,61),(931,42),(935,25),(937,1.3)])
    return 627.5-radius,627.5+radius


def head_section(y):
    front = head_contour(y)
    if not front:
        return None
    row = face_row(y)
    rear = alpha_span(min(432,max(14,row)), REAR-220, REAR+220)
    if not rear:
        rear=(REAR-1,REAR+1)
    return front + rear


def head_depth(x, y, cosine):
    _,fore,aft=side_section(y)
    center=(fore+aft)/2
    if cosine < 0:
        return center-(aft-center)*cosine
    ridge=19*gaussian(629,y,629,672,39,104)+32*gaussian(629,y,629,734,48,43)
    ridge+=9*gaussian(629,y,629,825,116,34)+16*gaussian(629,y,628,889,111,54)
    relief = 19*gaussian(x,y,629,672,39,104) + 32*gaussian(x,y,629,734,48,43)
    relief += 9*gaussian(x,y,629,825,116,34) + 16*gaussian(x,y,628,889,111,54)
    for eye in (486,773):
        relief += 7*gaussian(x,y,eye,480,99,37) - 9*gaussian(x,y,eye,571,81,34)
        relief += 6*gaussian(x,y,eye,627,65,54) + 9*gaussian(x,y,eye,722,87,52)
    relief+=9*gaussian(x,y,481,289,181,113)+8*gaussian(x,y,737,165,145,106)
    return center-max(1,center-fore-ridge)*cosine**.67-relief*cosine*cosine


fixed_head = (("Head",1),)


def head_surface(x,y,radius,c,front,rear):
    # Restrict colour dilation to the outside hair silhouette. The white of
    # each eye and the skin highlights are part of the original portrait.
    if abs(c)<.30 and y<582:
        front=face_samples[y,min(1253,max(0,round(front)))]
    back_row=max(14,min(432,round(face_row(y))))
    if 292 < back_row < 428:
        # The separate ears own the skin in the rear study; project only the
        # central hair onto the back of the head behind them.
        offsets=np.flatnonzero(rear_hair[back_row,REAR-202:REAR+203])-202
        if len(offsets)>4:
            rear=REAR+min(offsets[-1]-2,max(offsets[0]+2,CX-face_x(x)))
    return (point(face_x(x),face_row(y),head_depth(x,y,c)),
            uv(portrait,front,y),uv(atlas,rear,back_row),
            fixed_head,(x,y,c))


head, landmarks = rings("01 | Oval face, forehead and swept hair", range(22,938,2), 192,
                         head_section, head_surface, head_mat)
head.shape_key_add(name="Basis")
blink = head.shape_key_add(name="Blink")
jaw = head.shape_key_add(name="Speaking")
for i,(x,y,c) in enumerate(landmarks):
    if c < .30:
        continue
    eye = min((485,776),key=lambda ex:abs(ex-x))
    extent = min(1, abs(x-eye)/79)
    lid_mask = (1-smooth((extent-.80)/.20)) * smooth((c-.30)/.2)
    if lid_mask and 512 < y < 715:
        up = smooth((y-512)/41)
        down = 1-smooth((y-675)/40)
        close_y = 650-12*(extent**2)
        blink.data[i].co.z -= (close_y-y)*FACE_Y*UNIT*.98*up*down*lid_mask
    jaw.data[i].co.z -= 15.5*FACE_Y*UNIT*gaussian(x,y,628,843,94,45)*c


for s,tag in ((-1,"L"),(1,"R")):
    center = 330 if s<0 else 925
    def ear_section(y, center=center, s=s):
        t=(y-680)/96
        radius=74*math.sqrt(max(.0004,1-t*t))
        center_x=center + s*(680-y)*.11
        lo,hi=(242,405) if s<0 else (851,1010)
        xs=np.flatnonzero(skin[y,lo:hi])+lo
        if len(xs)<3:
            return None
        left=max(center_x-radius,float(xs[0])+1)
        right=min(center_x+radius,float(xs[-1])-1)
        back_center=REAR - s*148
        return left,right,back_center-radius*.45,back_center+radius*.45
    def ear_surface(x,y,r,c,front,rear):
        d=-4-r*.47*c + 6*gaussian(x,y,center,671,36,50)*max(0,c)
        return (point(face_x(x),face_row(y),d),uv(portrait,front,y),
                uv(atlas,rear,face_row(y)),fixed_head,None)
    rings("02 | Ear "+tag,range(585,777,3),64,ear_section,ear_surface,head_mat)


def body_section(part,y,s):
    if part == "neck":
        a=lerp_table(y,[(417,34),(443,40),(463,39),(484,36)])
        return CX-a,CX+a,REAR-a,REAR+a
    if part == "body":
        a=lerp_table(y,[(438,53),(451,65),(464,75),(479,105),(495,112),
                         (531,110),(568,103),(590,107),(620,116),(670,128),(712,123)])
        lo,hi=-a,a
    elif part == "hips":
        a=lerp_table(y,[(684,103),(711,110),(731,112),(746,105),(753,79),(758,10)])
        lo,hi=-a,a
    elif part in ("arm","thumb"):
        inside=lerp_table(y,[(471,94),(490,90),(516,94),(551,100),(569,103),
                              (580,110),(595,117),(624,132),(650,145),(690,174),(767,209)])
        outside=lerp_table(y,[(471,98),(480,120),(497,143),(530,177),
                               (590,237),(647,272),(770,299)])
        lo,hi=(inside,outside) if s==1 else (-outside,-inside)
    else:
        lo,hi=(1,157) if s==1 else (-157,-1)
    front=alpha_span(y,CX+lo,CX+hi)
    back=alpha_span(y,REAR-hi,REAR-lo)
    if not front:
        return None
    if part in ("arm","thumb") and y>=703:
        def fingers(span,side,is_rear=False):
            start,stop=map(round,span)
            xs=np.flatnonzero(silhouette[y,start:stop+1])+start
            clusters=[p for p in np.split(xs,np.where(np.diff(xs)>2)[0]+1) if len(p)>=2]
            if not clusters:
                return None
            if len(clusters)>1:
                ordered=clusters if side>0 else clusters[::-1]
                branch=ordered[0 if part=="thumb" else -1]
                return float(branch[0]),float(branch[-1])
            if part=="thumb":
                if y>736 and not is_rear:
                    return None
                if side>0:
                    return start, start+(stop-start)*.43
                return stop-(stop-start)*.43,stop
            return span
        front=fingers(front,s)
        back=fingers(back,-s,True) if back else None
        if not front:
            return None
    if back is None:
        back=(REAR-(front[1]-CX),REAR-(front[0]-CX))
    return front + back


def body_depth(part,y,r,c):
    if part == "neck":
        return 3-r*.93*c
    if part == "body":
        d=lerp_table(y,[(438,40),(463,53),(494,68),(540,76),(620,79),(676,82),(712,74)])
        return 6-d*math.copysign(abs(c)**.81,c)
    if part == "hips":
        d=lerp_table(y,[(684,65),(710,67),(731,62),(747,46),(759,10)])
        return 7-d*c
    if part in ("arm","thumb"):
        offset=lerp_table(y,[(470,2),(596,5),(681,-5),(767,-13)])
        return offset-r*(.78 if y>681 else .96)*c
    if y>939:
        front=lerp_table(y,[(939,43),(952,52),(972,77),(991,99),(1012,96)])
        return 1-(front if c>=0 else 47)*c
    return 5-r*lerp_table(y,[(694,1.08),(727,1.20),(840,1.07),(941,.90)])*c


def binding(part,y,tag):
    if part == "neck":
        return blend_bones(y,440,483,"Head","Chest")
    if part == "body":
        return blend_bones(y,604,704,"Chest","Pelvis")
    if part == "hips":
        return (("Pelvis",1),)
    if part in ("arm","thumb"):
        if y<532:
            return blend_bones(y,470,517,"Chest","UpperArm."+tag)
        if y<647:
            return blend_bones(y,575,616,"UpperArm."+tag,"Forearm."+tag)
        return blend_bones(y,669,692,"Forearm."+tag,"Hand."+tag)
    if y<778:
        return blend_bones(y,702,759,"Pelvis","Thigh."+tag)
    if y<900:
        return blend_bones(y,824,866,"Thigh."+tag,"Shin."+tag)
    return blend_bones(y,937,964,"Shin."+tag,"Foot."+tag)


def body_part(name,part,rows,s=0):
    tag="R" if s>0 else "L"
    def section(y):
        return body_section(part,y,s)
    def surface(x,y,r,c,front,rear):
        front_row,back_row=y,y
        if part=="neck":
            front_row=max(447,min(475,y))
            front=CX+(front-CX)*.74
            back_row=430
            rear=REAR+(rear-REAR)*.58
        return (point(x,y,body_depth(part,y,r,c)),uv(atlas,front,front_row),
                uv(atlas,rear,back_row),binding(part,y,tag),None)
    mat=skin_mat if part=="neck" else pants_mat if part in ("hips","leg") else cloth_mat
    return rings(name,rows,72,section,surface,mat)[0]


body_part("03 | Neck","neck",range(417,485,2))
body_part("04 | Open blue bomber, zipper and shirt","body",range(438,712,2))
body_part("05 | Trouser seat","hips",range(684,760,2))
for sign, tag in ((-1,"L"),(1,"R")):
    body_part("06 | Sleeve and hand "+tag,"arm",range(471,765,2),sign)
    body_part("07 | Thumb "+tag,"thumb",range(703,742),sign)
    body_part("08 | Trouser leg and shoe "+tag,"leg",range(698,1012,2),sign)


def opening():
    # A narrow mouth interior opens along the exact smile curve of the approved face.
    m=bpy.data.materials.new("Mouth | warm inner lip")
    m.use_nodes=True
    bsdf=m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value=(.11,.025,.019,1)
    bsdf.inputs["Roughness"].default_value=.89
    bsdf.inputs["Specular IOR Level"].default_value=.1
    verts,faces,delta=[],[],[]
    for i in range(37):
        t=i/36
        x=548+165*t
        guess=797+28*math.sin(math.pi*t)**.81
        y=round(guess)-3+int(np.argmin(rgb[round(guess)-3:round(guess)+4,round(x),1]))
        left,right=head_contour(y)
        c=math.sqrt(max(0,1-((2*x-left-right)/(right-left))**2))
        d=head_depth(x,y,c)-.9
        for k in (0,1):
            verts.append(point(face_x(x),face_row(y),d))
            delta.append(k*13*math.sin(math.pi*t)**.8*FACE_Y*UNIT)
        if i:
            faces.append((2*i-2,2*i-1,2*i+1,2*i))
    mouth=mesh_object("09 | Articulated smile",verts,faces,m,[],[],[],[fixed_head]*len(verts))
    mouth.shape_key_add(name="Basis")
    opened=mouth.shape_key_add(name="Open")
    for v,dy in zip(opened.data,delta):
        v.co.z-=dy
    return opened


mouth_open=opening()


def solve_arm(tag,wrist,pole,direction,frame):
    names=[p+"."+tag for p in ("UpperArm","Forearm","Hand")]
    shoulder,elbow0,wrist0=[joints[name] for name in names]
    upper,fore=elbow0-shoulder,wrist0-elbow0
    axis=(wrist-shoulder).normalized()
    distance=max(abs(upper.length-fore.length)+.0001,
                 min((wrist-shoulder).length,upper.length+fore.length-.0001))
    wrist=shoulder+axis*distance
    pole=pole-shoulder
    across=(pole-axis*pole.dot(axis)).normalized()
    along=(upper.length_squared-fore.length_squared+distance**2)/(2*distance)
    elbow=shoulder+axis*along+across*math.sqrt(max(0,upper.length_squared-along**2))
    q1=upper.rotation_difference(elbow-shoulder)
    q2=fore.rotation_difference(wrist-elbow)
    initial=Vector((.44 if tag=="R" else -.44,-.07,-1))
    q3=initial.rotation_difference(direction)
    for name,q in zip(names,(q1,q1.inverted()@q2,q2.inverted()@q3)):
        b=rig.pose.bones[name]
        q.make_compatible(b.rotation_quaternion)
        b.rotation_quaternion=q
        b.keyframe_insert(data_path="rotation_quaternion",frame=frame,group=name)


def rotate(name,angles,frame):
    b=rig.pose.bones[name]
    b.rotation_mode="XYZ"
    b.rotation_euler=[math.radians(a) for a in angles]
    b.keyframe_insert(data_path="rotation_euler",frame=frame,group=name)


loops=(("ready",1,36),("thinking",38,28),("speaking",67,28),("documents",96,42))
page_keys=make_folder(scene, 96)
for state,start,count in loops:
    scene.timeline_markers.new(state,frame=start)
    for i in range(count+1):
        frame=start+i
        wave=math.sin(math.tau*i/count)
        breath=.5-.5*math.cos(math.tau*i/count)
        thinks,answers=state=="thinking",state=="speaking"
        reading=state=="documents"
        if reading:
            turn=turn_progress(i/count)
            page_keys.eval_time=120*turn
            page_keys.keyframe_insert(data_path="eval_time",frame=frame)
            rotate("Chest",(2+.45*wave,0,.65*wave),frame)
            rotate("Neck",(3,0,0),frame)
            rotate("Head",(7.5+1.6*breath,.8*wave,-3.2+6.4*turn),frame)
        else:
            rotate("Chest",(.75*wave,.45*breath,1.0*wave),frame)
            rotate("Neck",((-1.8 if thinks else 0)+.55*breath,0,0),frame)
            rotate("Head",((3.4 if thinks else 0)+(1.7 if answers else .65)*wave,
                           (-3.0 if thinks else -.6)*breath,(-5 if thinks else 0)+1.5*wave),frame)
        blink.value=max(0,1-abs(i-round(count*.72))/1.4)
        speak=(.5-.5*math.cos(6*math.pi*i/count)) if answers else 0
        jaw.value=mouth_open.value=speak
        for key in (blink,jaw,mouth_open):
            key.keyframe_insert(data_path="value",frame=frame)
        for sign, tag in ((-1,"L"),(1,"R")):
            wrist=point(CX+sign*(158+2*wave),712+2*breath,-16+2*wave)
            pole=point(CX+sign*175,592,28)
            fingers=Vector((sign*.08,-.07,-1))
            if reading and sign==-1:
                wrist=point(CX-133,616,-138)
                pole=point(CX-185,642,-20)
                fingers=Vector((.48,-.68,.52))
            elif reading:
                wrist=point(CX+140-103*turn,604-25*math.sin(math.pi*turn),-143-37*math.sin(math.pi*turn))
                pole=point(CX+173,642,-25)
                fingers=Vector((-.58,-.62,.53))
            elif thinks and sign==1:
                wrist=point(CX+118+2*wave,502+3*breath,-99)
                pole=point(CX+168,599,-10)
                fingers=Vector((-.43,-.18,.88))
            elif answers and sign==-1:
                wrist=point(CX-172-10*wave,569-8*breath,-109-5*wave)
                pole=point(CX-172,617,0)
                fingers=Vector((-.71,-.52,.31+.22*breath))
            solve_arm(tag,wrist,pole,fingers,frame)
rig.animation_data.action.name="Workspace | wait, think, answer, read documents"


def aim(obj,target):
    obj.rotation_euler=(Vector(target)-obj.location).to_track_quat("-Z","Y").to_euler()


def softbox(name,position,energy,size,color):
    data=bpy.data.lights.new(name,"AREA")
    data.energy,data.size,data.color=energy,size,color
    obj=bpy.data.objects.new(name,data)
    scene.collection.objects.link(obj)
    obj.location=position
    aim(obj,(0,0,1))


softbox("Softbox | key",(-2.1,-3,3.3),180,3.0,(1,.95,.89))
softbox("Softbox | fill",(2.5,-2.5,2.3),130,3.5,(.88,.94,1))
softbox("Softbox | rim",(.3,2,3.3),165,2.4,(.85,.93,1))
world=bpy.data.worlds.new("Neutral studio")
world.use_nodes=True
world.node_tree.nodes["Background"].inputs["Color"].default_value=(.8,.83,.88,1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value=.58
scene.world=world
camdata=bpy.data.cameras.new("Orthographic portrait")
camdata.type,camdata.ortho_scale="ORTHO",2.13
cam=bpy.data.objects.new("Camera | desktop",camdata)
scene.collection.objects.link(cam)
cam.location=(0,-5.5,1.008)
aim(cam,(0,0,1.008))
scene.camera=cam
scene.render.engine="CYCLES"
scene.cycles.samples=8
scene.cycles.use_denoising=True
scene.render.threads_mode="FIXED"
scene.render.threads=6
scene.render.film_transparent=True
scene.render.image_settings.file_format="PNG"
scene.render.image_settings.color_mode="RGBA"
scene.render.resolution_x,scene.render.resolution_y=320,432
scene.render.resolution_percentage=100
scene.view_settings.view_transform="Standard"
scene.render.fps=14
scene.frame_start,scene.frame_end=1,137
scene.frame_set(1)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=="VIEW_3D":
            area.spaces.active.region_3d.view_perspective="CAMERA"
            area.spaces.active.overlay.show_overlays=False
            area.spaces.active.shading.type="MATERIAL"
bpy.context.preferences.filepaths.save_version=0
if hasattr(bpy.context.preferences.filepaths,"file_preview_type"):
    bpy.context.preferences.filepaths.file_preview_type="NONE"
bpy.ops.wm.save_as_mainfile(filepath=str(out/"mini-assistant.blend"),compress=True)


def render(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    scene.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)


if "--preview" in args:
    scene.render.resolution_x,scene.render.resolution_y=610,824
    scene.cycles.samples=12
    for name,frame,angle in (("front",1,0),("thinking",48,0),("speaking",74,0),
                             ("three-quarter",1,32),("reverse",1,180)):
        scene.frame_set(frame)
        angle=math.radians(angle)
        cam.location=(5.5*math.sin(angle),-5.5*math.cos(angle),1.008)
        aim(cam,(0,0,1.008))
        render(out/"preview"/(name+".png"))
    cam.location=(0,-5.5,1.008)
    aim(cam,(0,0,1.008))
    scene.render.resolution_x,scene.render.resolution_y=320,432
    scene.cycles.samples=8
if "--render" in args:
    selection = args[args.index("--state") + 1] if "--state" in args else None
    if selection and selection not in {name for name, _, _ in loops}:
        raise ValueError(f"Unknown animation: {selection}")
    for name,start,count in loops:
        if selection and name != selection:
            continue
        for i in range(count):
            scene.frame_set(start+i)
            render(out/"renders"/name/f"{i:02d}.png")

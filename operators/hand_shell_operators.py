import bmesh
import math

import mathutils

from ..utils import *
from .clipping_detection_operators import PART_VGS

# 左右手各做一个罩体，三个方向都取自对应侧的骨骼（.L / .R）
SHELL_SIDES = ("L", "R")

# 各辅助函数的默认侧，正文里都是显式传 side
SHELL_SIDE = "L"

# 定方向必须用到的骨骼：左右手的 手首 与 中指１
REQUIRED_SHELL_BONES = tuple(
    f"{name}.{side}" for side in SHELL_SIDES for name in ("手首", "中指１")
)

# 生成的罩体上打这个标记：它自己也挂在骨架下，下次收集手部顶点必须跳过它
SHELL_TAG = "mmd_motion_tools_hand_shell"

# 和 classify_vertices 的默认阈值保持一致：同一部件内权重和超过它才算这个部件
HAND_WEIGHT_THRESHOLD = 0.25


class GenerateHandShellOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.generate_hand_shell"
    bl_label = "生成罩体"
    bl_description = "根据手部网格生成椭圆罩体，并通过自动权重将其绑定到骨架。"
    bl_options = {'REGISTER', 'UNDO'}

    vertex_count: bpy.props.IntProperty(
        name="顶点数",
        description="单个罩体的顶点数量（取最接近该值的球面细分）",
        default=5000,
        min=12,
        max=100000,
    )

    length_ratio: bpy.props.FloatProperty(
        name="长",
        description="长度的缩放比例",
        default=1.0,
        min=0.05,
        max=10.0,
    )

    width_ratio: bpy.props.FloatProperty(
        name="宽",
        description="宽度的缩放比例",
        default=1.0,
        min=0.05,
        max=10.0,
    )

    height_ratio: bpy.props.FloatProperty(
        name="高",
        description="高度的缩放比例",
        default=1.0,
        min=0.05,
        max=10.0,
    )

    def check_props(self, context):
        """执行前的校验，返回 (骨架, 网格列表)；不满足条件时返回 None"""
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, '未启用mmd_tools插件！')
            return None

        obj = context.active_object
        if obj is None:
            self.report({'ERROR'}, '请选择MMD模型！')
            return None

        root = find_pmx_root_with_child(obj)
        if root is None:
            self.report({'ERROR'}, '请选择MMD模型！')
            return None

        armature = find_pmx_armature(root)
        if armature is None:
            self.report({'ERROR'}, '未检测到骨架！')
            return None

        missing_bones = [name for name in REQUIRED_SHELL_BONES if armature.data.bones.get(name) is None]
        if missing_bones:
            self.report({'ERROR'}, '缺少手部骨骼：' + '、'.join(missing_bones))
            return None

        mesh_objects = find_pmx_objects(armature)
        if not mesh_objects:
            self.report({'ERROR'}, '未检测到网格！')
            return None

        vg_names = set()
        for mesh_obj in mesh_objects:
            if mesh_obj.get(SHELL_TAG):
                continue
            vg_names.update(vg.name for vg in mesh_obj.vertex_groups)
        missing_groups = [name for name in REQUIRED_SHELL_BONES if name not in vg_names]
        if missing_groups:
            self.report({'ERROR'}, '网格缺少顶点组：' + '、'.join(missing_groups))
            return None

        return armature, mesh_objects

    def execute(self, context):
        result = self.check_props(context)
        if result is None:
            return {'CANCELLED'}
        armature, mesh_objects = result

        # 左右手逻辑完全对称
        messages = []
        for side in SHELL_SIDES:
            message = self.build_side(context, armature, mesh_objects, side)
            if message:
                messages.append(message)

        if not messages:
            return {'CANCELLED'}

        self.report({'INFO'}, '已生成 ' + '；'.join(messages))
        return {'FINISHED'}

    def build_side(self, context, armature, mesh_objects, side):
        """生成一侧罩体，返回报告文案；某一侧做不了时返回 None"""
        frame = build_ortho_frame(armature, side)
        if frame is None:
            self.report({'WARNING'}, f'{side} 侧手部骨骼缺失或方向异常（手首/中指１），已跳过')
            return None
        origin, axis_a, axis_b, axis_c = frame

        points = collect_hand_points(armature, mesh_objects, side)
        if not points:
            self.report({'WARNING'}, f'{side} 侧没有找到手部顶点，已跳过')
            return None

        # 每个方向各取一次投影范围：差值就是长/宽/高，中点用来定位罩体中心
        sizes = []
        center = origin.copy()
        for axis, ratio in zip((axis_a, axis_b, axis_c),
                               (self.length_ratio, self.width_ratio, self.height_ratio)):
            low, high = get_projection_range(points, axis, origin)
            sizes.append(max(high - low, 1e-5) * ratio)
            center += axis * ((low + high) * 0.5)

        axes = (axis_a, axis_b, axis_c)
        half_sizes = [size * 0.5 for size in sizes]
        vertices, faces = build_ellipsoid_geometry(center, axes, half_sizes, self.vertex_count)

        # 生成后、赋权重前：把罩体顺着半径推出去，直到完全包住手（相当于 Grab，但整体平滑）
        vertices, inflation = enclose_hand_points(vertices, points, center, axes, half_sizes, faces)

        # 上面算的都是骨架空间，这里统一转到世界空间：
        # 物体矩阵保持单位阵，骨架修改器和自动权重都按世界坐标工作，最省事也最不容易错
        world = armature.matrix_world
        vertices = [world @ vertex for vertex in vertices]

        shell = self.create_object(context, armature, f"手部罩体.{side}", vertices, faces)
        self.bind_auto_weights(context, armature, shell)

        return (f'{shell.name}：{len(vertices)} 顶点，'
                f'长 {sizes[0]:.4f} / 宽 {sizes[1]:.4f} / 高 {sizes[2]:.4f}，'
                f'外扩 {inflation:.2f}×')

    def create_object(self, context, armature, name, vertices, faces):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
        mesh.update()

        # 球面的绕序容易写反，交给 bmesh 统一重算法线，保证朝外
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()

        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.update()

        shell = bpy.data.objects.new(name, mesh)
        # 打上标记：它挂在骨架下、又有手部顶点组，收集手部顶点时必须跳过
        shell[SHELL_TAG] = True

        # 挂在骨架所在的集合里，跟模型网格放一起
        collection = armature.users_collection[0] if armature.users_collection else context.scene.collection
        collection.objects.link(shell)
        return shell

    def bind_auto_weights(self, context, armature, shell):
        """
        先选罩体、再选骨架（骨架是活动物体），用自动权重设父级

        parent_set 会保留世界变换，所以物体矩阵之后仍是单位阵，
        罩体顶点本来就是世界坐标，位置不会跑。
        """
        ensure_object_mode(context)
        for obj in context.view_layer.objects:
            obj.select_set(False)
        shell.select_set(True)
        armature.select_set(True)
        context.view_layer.objects.active = armature

        try:
            bpy.ops.object.parent_set(type='ARMATURE_AUTO')
        except RuntimeError as error:
            # 骨热蒙皮偶尔会失败（网格有重叠面等），至少保证骨架修改器还在
            self.report({'WARNING'}, f'自动权重失败：{error}')

        if not any(modifier.type == 'ARMATURE' and modifier.object == armature
                   for modifier in shell.modifiers):
            modifier = shell.modifiers.new(name="Armature", type='ARMATURE')
            modifier.object = armature


def get_side_hand_vgs(side=SHELL_SIDE):
    """取 HAND 部件里属于某一侧的顶点组名"""
    suffix = "." + side
    return {name for name in PART_VGS["HAND"] if name.endswith(suffix)}


def collect_hand_points(armature, mesh_objects, side=SHELL_SIDE):
    """
    收集手部顶点坐标（骨架空间）

    之前生成的罩体身上也有手部顶点组（自动权重会给所有骨骼建组），
    所以要按 SHELL_TAG 跳过，否则反复点会把手部范围越撑越大。
    """
    hand_vgs = get_side_hand_vgs(side)

    # 骨架空间的坐标：子网格自己的世界矩阵换算完，再退回骨架的局部空间
    to_armature = armature.matrix_world.inverted()
    points = []

    for mesh_obj in mesh_objects:
        if mesh_obj.get(SHELL_TAG):
            continue

        mesh = mesh_obj.data
        matrix = to_armature @ mesh_obj.matrix_world

        # 顶点上存的是顶点组索引，先把这一侧的手部组索引挑出来
        hand_group = {vg.index for vg in mesh_obj.vertex_groups if vg.name in hand_vgs}
        if not hand_group:
            continue

        for vertex in mesh.vertices:
            weight = 0.0
            for element in vertex.groups:
                if element.group in hand_group:
                    weight += element.weight
            if weight > HAND_WEIGHT_THRESHOLD:
                points.append(matrix @ vertex.co)

    return points


def get_dummy_direction(armature, side=SHELL_SIDE):
    """
    求「手掌朝向」：不用模型自带的 ダミー 骨骼，按创建 dummy 骨骼时的算法算出来

    取手首.head 和 中指１.head，把中指头压到手首所在的 XZ 平面上（Y 取手首的），
    再在 XZ 平面内转 90°：左手 (z, 0, -x)，右手 (-z, 0, x)。
    这与 mmd_kafei_tools 里 create_dummy_bone 的 callback1 / callback2 一致。
    """
    bones = armature.data.bones
    wrist = bones.get(f"手首.{side}")
    middle = bones.get(f"中指１.{side}")
    if wrist is None or middle is None:
        return None

    wrist_head = wrist.head_local
    middle_head = middle.head_local
    # 压到和手首同一个 Y 高度，方向就落在 XZ 平面里了
    flattened = mathutils.Vector((middle_head.x, wrist_head.y, middle_head.z))
    direction = flattened - wrist_head
    if direction.length < 1e-6:
        return None

    # 左手的 callback2 是 (z, 0, -x)，右手的 callback1 是 (-z, 0, x)
    if side == "L":
        rotated = mathutils.Vector((direction.z, 0.0, -direction.x))
    else:
        rotated = mathutils.Vector((-direction.z, 0.0, direction.x))
    if rotated.length < 1e-6:
        return None
    return rotated.normalized()


def build_ortho_frame(armature, side=SHELL_SIDE):
    """
    按骨骼方向建正交坐标系，返回 (origin, 方向A, 方向B, 方向C)

    长 A：手首.head → 中指１.tail
    高 C：由手首.head 和 中指１.head 算出来的手掌朝向（见 get_dummy_direction）
    宽 B：与 A、C 都垂直，直接取 A × C

    A 和 C 本身不一定严格垂直，所以再用 A × B 反推一次 C（等于把 C 里平行于 A 的
    分量去掉），保证三个轴两两垂直、长度测量和椭球拉伸用的是同一套轴。
    骨骼缺失或方向退化（长度≈0、A 与 C 平行）时返回 None。
    """
    bones = armature.data.bones
    wrist = bones.get(f"手首.{side}")
    middle = bones.get(f"中指１.{side}")
    if wrist is None or middle is None:
        return None

    dummy_direction = get_dummy_direction(armature, side)
    if dummy_direction is None:
        return None

    origin = wrist.head_local.copy()
    length_axis = middle.tail_local - wrist.head_local
    if length_axis.length < 1e-6:
        return None
    length_axis.normalize()

    # 宽度方向：和长、高都垂直。A 与 C 平行时叉积为零，方向不确定
    width_axis = length_axis.cross(dummy_direction)
    if width_axis.length < 1e-6:
        return None
    width_axis.normalize()

    # 反推与 A、B 都垂直的 C（= 去掉 C 里平行于 A 的分量），再对齐手掌朝向
    height_axis = length_axis.cross(width_axis)
    if height_axis.dot(dummy_direction) < 0.0:
        height_axis = -height_axis

    return origin, length_axis, width_axis, height_axis


def get_projection_range(points, axis, origin):
    """点集在 axis 方向上的投影范围 (最小值, 最大值)"""
    values = [(point - origin).dot(axis) for point in points]
    return min(values), max(values)


def fit_sphere_subdivision(target_count):
    """
    挑一组最接近目标顶点数的球面细分，返回 (经线段数, 纬线数)

    球面顶点数 = 经线段数 × (纬线数 - 1) + 2（两极各一个顶点），任意数不一定凑得出来。
    光看误差会选出「3 条经线 × 334 条纬线」这种极端形状，所以经线段数限制在
    2 倍纬线数附近（Blender 自带 UV 球也是这个比例），先比误差、再比偏离程度。
    """
    target = max(14, int(target_count))
    best = None
    for rings in range(3, 257):
        for segments in range(max(3, rings * 2 - 2), rings * 2 + 3):
            count = segments * (rings - 1) + 2
            score = (abs(count - target), abs(segments - rings * 2))
            if best is None or score < best[0]:
                best = (score, segments, rings)
    return best[1], best[2]


def build_ellipsoid_geometry(center, axes, half_sizes, target_count):
    """
    生成椭球网格数据，返回 (顶点列表, 面列表)

    先造单位球再按三个半径拉伸：坐标 = 中心 + A*u*半长 + B*v*半宽 + C*w*半高
    """
    segments, rings = fit_sphere_subdivision(target_count)
    axis_a, axis_b, axis_c = axes
    half_a, half_b, half_c = half_sizes

    # 北极单独一个顶点
    vertices = [center + axis_c * half_c]
    for ring in range(1, rings):
        theta = math.pi * ring / rings
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        for segment in range(segments):
            phi = 2.0 * math.pi * segment / segments
            vertices.append(center
                            + axis_a * (sin_theta * math.cos(phi) * half_a)
                            + axis_b * (sin_theta * math.sin(phi) * half_b)
                            + axis_c * (cos_theta * half_c))
    # 南极
    vertices.append(center - axis_c * half_c)
    south = len(vertices) - 1

    def index(ring, segment):
        return 1 + (ring - 1) * segments + segment % segments

    faces = []
    # 顶盖三角面
    for segment in range(segments):
        faces.append((0, index(1, segment), index(1, segment + 1)))
    # 中间的四边面
    for ring in range(1, rings - 1):
        for segment in range(segments):
            faces.append((
                index(ring, segment),
                index(ring, segment + 1),
                index(ring + 1, segment + 1),
                index(ring + 1, segment),
            ))
    # 底盖三角面
    for segment in range(segments):
        faces.append((south, index(rings - 1, segment + 1), index(rings - 1, segment)))

    return vertices, faces


def build_vertex_neighbors(vertex_count, faces):
    """从面表建顶点邻接表（拉普拉斯平滑用）"""
    neighbors = [set() for _ in range(vertex_count)]
    for face in faces:
        for index, vertex_index in enumerate(face):
            neighbors[vertex_index].add(face[index - 1])
            neighbors[vertex_index].add(face[(index + 1) % len(face)])
    return [sorted(near) for near in neighbors]


def to_unit_space(coords, center, axes, half_sizes):
    """
    把坐标换算到「单位球空间」：减中心 → 投影到三个轴上 → 除以半轴

    在这个空间里，初始椭球表面到中心的距离正好是 1，
    于是「半径」就等于「相对椭球放大了多少倍」，径向缩放直接乘这个系数即可。
    """
    # 延迟导入，避免拖慢插件加载速度
    import numpy as np

    axis_a, axis_b, axis_c = (np.array(axis[:]) for axis in axes)
    relative = np.asarray([tuple(co) for co in coords]) - np.array(center[:])
    projected = np.stack([relative @ axis_a, relative @ axis_b, relative @ axis_c], axis=1)
    return projected / np.array([half_sizes[0], half_sizes[1], half_sizes[2]])


def scale_radially(vertices, center, factors):
    """按方向把顶点沿半径缩放：新位置 = 中心 + (原位置 - 中心) × 系数"""
    import numpy as np

    origin = np.array(center[:])
    moved = origin + (np.array([tuple(vertex) for vertex in vertices]) - origin) * np.asarray(factors)[:, None]
    return [mathutils.Vector(tuple(row)) for row in moved]


def smooth_scale_field(scale, faces, iterations):
    """在网格上做几轮拉普拉斯平滑，让半径场过渡圆润"""
    import numpy as np

    neighbors = build_vertex_neighbors(len(scale), faces)
    for _ in range(iterations):
        averaged = scale.copy()
        for index, near in enumerate(neighbors):
            if near:
                averaged[index] = 0.5 * scale[index] + 0.5 * scale[near].mean()
        scale = averaged
    return scale


def enclose_hand_points(vertices, points, center, axes, half_sizes, faces,
                        margin=1.06, sigma=0.7, smooth_iterations=5):
    """
    把罩体沿半径方向推出去，直到手部顶点全部落在里面（类似雕刻的 Grab，但整体是平滑的）

    做法分三步：
    1. 把罩体和手部点都换算到「单位球空间」：罩体顶点半径为 1，手部点到原点的距离 r
       就是它需要的半径
    2. r > 1 的点构成一个「这里需要鼓起多少」的场，按角度做高斯衰减后取最大值，
       得到每个罩体顶点方向的缩放系数（高斯很宽，相邻顶点几乎一起鼓，所以不会扎尖）
    3. 在网格上做几轮拉普拉斯平滑，最后夹一层下限，保证平滑没把该鼓的地方压回去

    返回 (新顶点列表, 最大缩放系数)
    """
    import numpy as np

    unit_vertices = to_unit_space(vertices, center, axes, half_sizes)
    unit_points = to_unit_space(points, center, axes, half_sizes)

    radii = np.linalg.norm(unit_points, axis=1)
    outside = radii > 1.0
    if not outside.any():
        return vertices, 1.0

    directions_v = unit_vertices / np.linalg.norm(unit_vertices, axis=1, keepdims=True)
    directions_p = unit_points[outside] / radii[outside, None]
    excess = radii[outside] - 1.0

    # 1 - cos(角度) ≈ 角度²/2，所以 exp(-(1-dot)/sigma²) 就是一个角度方向的宽高斯
    needed = np.zeros(len(directions_v))
    for start in range(0, len(directions_v), 256):
        block = directions_v[start:start + 256] @ directions_p.T
        kernel = np.exp(-(1.0 - block) / (sigma * sigma))
        needed[start:start + 256] = (kernel * excess[None, :]).max(axis=1) * margin

    scale = 1.0 + needed
    if smooth_iterations > 0:
        # 平滑会把波峰压矮，所以平滑后再夹一层下限，保证每个方向都不小于需求
        scale = np.maximum(smooth_scale_field(scale, faces, smooth_iterations), 1.0 + needed)

    return scale_radially(vertices, center, scale), float(scale.max())

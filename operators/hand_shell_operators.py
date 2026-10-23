"""手部罩体：穿模检测时包裹手部的罩体

罩体由「正方体 + 3 级 Catmull-Clark 表面细分」应用得到（384 面），再依次添加：
    - Armature：顶点组权重 手首.L / 手首.R，跟随临时骨架
    - Shrinkwrap：目标是对应侧的 HAND 临时网格
    - Smooth：5 次迭代、系数 1.0，把收缩后的表面抹平

创建时按角色静置姿态的手部网格确定罩体中心与初始大小，并加一个放大用的形态键
"Inflate"（值 1.0 = 以自身中心放大 2 倍，初始为 0）。检测时逐帧先把 Shrinkwrap.offset
（步长 0.001，软上限 0.015）调到刚好包住手；到上限还包不住，就把 "Inflate"
每步 +0.01（最多 0.15）把面撑大；形态键也到顶才继续加大 offset。
offset 和形态键每帧都打关键帧。

罩体放在自己的「罩体」集合里，不属于「穿模检测临时集合」：检测结束会保留下来，
「删除临时对象」也清不到它。同一个集合里还放着它专用的手部代理网格（不精简、隐藏）。
罩体和这块代理网格跟其它部件代理一样，检测期间绑在精简后的临时骨架上（临时骨架建在
「穿模检测临时集合」里）；检测结束时调用方会把它们的骨架修改器改指回原骨架，
每次执行检测前整组重建。
"""

import bmesh
from mathutils.bvhtree import BVHTree

from ..utils import *

# 一侧罩体的目标细分级别：3 级 Catmull-Clark 后约 384 面
HAND_SPHERE_SUBDIV_LEVELS = 3

# Smooth 修改器参数：5 次迭代、系数 1.0
HAND_SPHERE_SMOOTH_ITERATIONS = 5
HAND_SPHERE_SMOOTH_FACTOR = 1.0

# Shrinkwrap 偏移的单次调整幅度
HAND_SPHERE_OFFSET_STEP = 0.001

# Shrinkwrap 偏移的软上限 0.015：到这里还包不住手，就先去加大「放大」形态键，
# 而不是继续把 offset 往上推（offset 太大，贴着手的壳体会被撑厚，碰撞判定跟着失真）
HAND_SPHERE_OFFSET_SOFT_MAX = 0.015
HAND_SPHERE_OFFSET_SOFT_MAX_STEPS = int(round(HAND_SPHERE_OFFSET_SOFT_MAX / HAND_SPHERE_OFFSET_STEP))

# 罩体自带的「放大」形态键：
# 值 1.0 时以自身中心为基准放大 2 倍
HAND_SPHERE_SCALE_KEY_NAME = "Inflate"
HAND_SPHERE_SCALE_FACTOR = 2.0

# 调形态键的步长与上限：每次 0.01，最多 0.15
# （内部按整数步数运算，避免反复加减 0.01 产生浮点误差）
HAND_SPHERE_SCALE_STEP = 0.01
HAND_SPHERE_SCALE_MAX = 0.15
HAND_SPHERE_SCALE_MAX_STEPS = int(round(HAND_SPHERE_SCALE_MAX / HAND_SPHERE_SCALE_STEP))

# 确认「再小一步就包不住」之后，隔几帧再复核一次：
# 每帧都试一次要多花一次依赖图求值（约 2.5 ms/帧），隔几帧复核足够跟上手部动作
HAND_SPHERE_SHRINK_RECHECK_FRAMES = 4

# 罩体对象上的标记，便于从集合里认出它
HAND_SPHERE_TAG = "mmd_motion_tools_hand_sphere"

HAND_SPHERE_SIDES = ("L", "R")

# 罩体自己的集合名：罩体不属于「穿模检测临时集合」，检测结束要保留
HAND_SPHERE_COLLECTION_NAME = "罩体"


def get_hand_local_cos(hand_obj, depsgraph=None):
    """
    取 HAND 临时网格当前帧的顶点坐标（物体局部空间）

    罩体对象和 HAND 网格用同一个 matrix_world，所以两者的局部坐标可以直接比较，
    不需要再转到世界空间。
    """
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    eval_obj = hand_obj.evaluated_get(depsgraph)
    return [vertex.co.copy() for vertex in eval_obj.data.vertices]


def measure_cos(cos):
    """坐标集的包围盒中心、最大半径和三个方向的尺寸"""
    low = Vector([min(co[i] for co in cos) for i in range(3)])
    high = Vector([max(co[i] for co in cos) for i in range(3)])
    center = (low + high) * 0.5
    radius = max((co - center).length for co in cos)
    # extents.xyz对应包围盒的xyz，即三个方向的尺寸
    extents = high - low
    return center, radius, extents


def create_cube_object(name, side, collection, temp_tag):
    """创建一个正方体网格对象，作为表面细分的输入（球形罩体实际为正方体+细分，编辑模式下视觉更好）"""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    # 临时标记 + 罩体自己的标记：重新检测时靠这两个标记把上一次的罩体认出来删掉
    obj[temp_tag] = True
    obj[HAND_SPHERE_TAG] = True
    obj["mmd_hand_sphere_side"] = side
    collection.objects.link(obj)
    return obj


def apply_subdivision(context, ball):
    """给正方体添加 3 级 Catmull-Clark 表面细分并应用"""
    modifier = ball.modifiers.new(name="Subdivision", type='SUBSURF')
    modifier.subdivision_type = 'CATMULL_CLARK'
    modifier.levels = HAND_SPHERE_SUBDIV_LEVELS
    modifier.render_levels = HAND_SPHERE_SUBDIV_LEVELS

    ensure_object_mode(context)
    select_only_objects(context, [ball])
    bpy.context.view_layer.objects.active = ball
    try:
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    finally:
        ball.select_set(False)
        if bpy.context.view_layer.objects.active is ball:
            bpy.context.view_layer.objects.active = None


def fit_ball_to_hand(mesh, hand_center, hand_radius):
    """
    把细分后的罩体缩放、平移到手部周围，保证静置姿态下完全罩住手

    用罩体表面顶点到罩体中心的最小距离来算缩放，而不是最大距离，
    这样罩体表面最「凹」的位置也不会落进手部最大半径以内。
    """
    cos = [vertex.co.copy() for vertex in mesh.vertices]
    if not cos:
        return

    low = Vector([min(co[i] for co in cos) for i in range(3)])
    high = Vector([max(co[i] for co in cos) for i in range(3)])
    ball_center = (low + high) * 0.5
    ball_min_radius = min((co - ball_center).length for co in cos)
    if ball_min_radius < 1e-9:
        return

    scale = hand_radius / ball_min_radius * 1.02
    for vertex in mesh.vertices:
        vertex.co = (vertex.co - ball_center) * scale + hand_center
    mesh.update()


def recalc_normals_and_smooth(mesh):
    """统一法线朝外，并设置平滑着色"""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.update()


def add_scale_shape_key(obj, name=HAND_SPHERE_SCALE_KEY_NAME, factor=HAND_SPHERE_SCALE_FACTOR):
    """
    给罩体加一个放大用的形态键（名字见 HAND_SPHERE_SCALE_KEY_NAME，即 "Inflate"）：
    值 1.0 时整个网格以自身中心为基准放大 factor 倍

    形态键里存的是绝对坐标，所以必须在网格形状定下来之后再建
    （也就是先贴手、缩放，再加这个键），否则键里记的是还没贴手的初始正方体。
    """
    mesh = obj.data
    obj.shape_key_add(name="Basis", from_mix=False)
    key = obj.shape_key_add(name=name, from_mix=False)

    if not mesh.vertices:
        return key

    low = Vector([min(vertex.co[i] for vertex in mesh.vertices) for i in range(3)])
    high = Vector([max(vertex.co[i] for vertex in mesh.vertices) for i in range(3)])
    center = (low + high) * 0.5

    for index, vertex in enumerate(mesh.vertices):
        key.data[index].co = center + (vertex.co - center) * factor

    # 只允许往「放大」的方向调
    key.slider_min = 0.0
    key.slider_max = 1.0
    key.value = 0.0
    return key


def create_side_sphere(context, armature, hand_obj, side, collection, temp_tag, hand_vertex_cos):
    """创建一侧的罩体，返回 HandSphere；手部数据不足时返回 None"""
    if len(hand_vertex_cos) < 3:
        return None

    hand_center, hand_radius, hand_extents = measure_cos(hand_vertex_cos)
    if hand_radius < 1e-6:
        return None

    name = f"手部罩体.{side}"
    ball = create_cube_object(name, side, collection, temp_tag)
    apply_subdivision(context, ball)

    # 细分后的罩体先罩住静置姿态的手，再挂修改器
    fit_ball_to_hand(ball.data, hand_center, hand_radius)
    recalc_normals_and_smooth(ball.data)

    # 「放大」形态键
    scale_key = add_scale_shape_key(ball)

    # 罩体跟随手首骨骼：所有顶点都绑到 手首.L / 手首.R
    vertex_group = ball.vertex_groups.new(name=f"手首.{side}")
    vertex_group.add(list(range(len(ball.data.vertices))), 1.0, 'REPLACE')

    # 和临时网格一样挂骨架修改器
    armature_modifier = ball.modifiers.new(name="mmd_armature", type='ARMATURE')
    armature_modifier.object = armature

    # 收缩包裹到对应手：Nearest Surface Point + 可调的 offset
    shrinkwrap = ball.modifiers.new(name="Shrinkwrap", type='SHRINKWRAP')
    shrinkwrap.target = hand_obj
    shrinkwrap.wrap_method = 'NEAREST_SURFACEPOINT'
    shrinkwrap.wrap_mode = 'ON_SURFACE'
    shrinkwrap.offset = 0.0

    # 平滑掉收缩包裹产生的褶皱
    smooth = ball.modifiers.new(name="Smooth", type='SMOOTH')
    smooth.iterations = HAND_SPHERE_SMOOTH_ITERATIONS
    smooth.factor = HAND_SPHERE_SMOOTH_FACTOR

    # 和 HAND 临时网格使用同一个物体矩阵，局部坐标才能直接比较
    ball.matrix_world = hand_obj.matrix_world.copy()

    # 偏移上限：手部自身尺寸已经足够大，按局部单位给一个宽松上限
    max_steps = max(50, int(max(hand_extents) / HAND_SPHERE_OFFSET_STEP))
    return HandSphere(ball, hand_obj, side, shrinkwrap, max_steps, scale_key)


class HandSphere:
    """
    一只手对应的罩体

    罩体通过 Armature 跟随手首，通过 Shrinkwrap 贴住对应手；每帧调整
    Shrinkwrap.offset 让罩体表面把所有手部顶点包在里面，并给 offset 打关键帧。
    offset 到软上限还包不住时，改为加大「放大」形态键，形态键也到上限才继续加 offset；
    这两条通道也是每帧打关键帧。
    """

    def __init__(self, obj, hand_obj, side, shrinkwrap, max_steps, scale_key=None):
        self.obj = obj
        self.hand_obj = hand_obj
        self.side = side
        self.shrinkwrap = shrinkwrap
        self.max_steps = max_steps
        self.offset_steps = 0
        # 「放大」形态键（没有就是不支持，所有形变退回只调 offset）
        self.scale_key = scale_key
        # 形态键当前步数，1 步 = HAND_SPHERE_SCALE_STEP（0.01）
        self.scale_steps = 0
        # 上一帧是否已经确认「再小一步就包不住」，用来省掉每帧的试探
        self.offset_minimized = False
        self.scale_minimized = False

    @property
    def scale_value(self):
        """「放大」形态键的当前值（没有形态键时按 0 算）"""
        return self.scale_key.value if self.scale_key is not None else 0.0

    def set_scale_steps(self, steps):
        """把「放大」形态键设成指定步数（1 步 = 0.01）；只写值，不触发依赖图"""
        self.scale_steps = max(0, min(int(steps), HAND_SPHERE_SCALE_MAX_STEPS))
        if self.scale_key is not None:
            self.scale_key.value = self.scale_steps * HAND_SPHERE_SCALE_STEP

    def keyframe(self, frame):
        """每帧给 offset 和「放大」形态键都打关键帧，拖时间轴就能看到它们怎么变"""
        self.shrinkwrap.keyframe_insert(data_path="offset", frame=frame)
        if self.scale_key is not None:
            self.scale_key.keyframe_insert(data_path="value", frame=frame)

    @staticmethod
    def _covers_hand(sphere_bvh, hand_bvh):
        """
        罩体是否包住手：直接看罩体和手有没有面相交

        和判断手部与其他部件穿模用的是同一套 BVH overlap。
        罩体整体包住手时两者表面不应相交；一旦相交，说明手从罩体里穿出来了。
        """
        return not sphere_bvh.overlap(hand_bvh)


def _apply_sphere_settings(settings_by_sphere):
    """
    同时设置多个罩体的 offset 和「放大」形态键，只做一次依赖图更新，再为每个罩体建 BVH

    settings_by_sphere 是 {HandSphere: (offset 步数, 形态键步数)}。
    返回 {HandSphere: 罩体 BVH}——键就是罩体本身，取用时不用再查对象名。
    """
    for sphere, (offset_steps, scale_steps) in settings_by_sphere.items():
        sphere.shrinkwrap.offset = offset_steps * HAND_SPHERE_OFFSET_STEP
        sphere.set_scale_steps(scale_steps)

    # 只有罩体自己的参数变了，依赖图里被标记的也只有罩体；取当前依赖图即可拿到新结果
    depsgraph = bpy.context.evaluated_depsgraph_get()

    return {
        sphere: BVHTree.FromObject(sphere.obj.evaluated_get(depsgraph), depsgraph)
        for sphere in settings_by_sphere
    }


def _evaluate_candidate(sphere, offset_steps, scale_steps, hand_bvh):
    """单只罩体试一组参数，返回 (是否包住手, 这组参数对应的罩体 BVH)"""
    sphere_bvh = _apply_sphere_settings({sphere: (offset_steps, scale_steps)})[sphere]
    return sphere._covers_hand(sphere_bvh, hand_bvh), sphere_bvh


def _grow_scale_until_covered(sphere, state, hand_bvh):
    """
    offset 顶到软上限还包不住时，往上找「最小的形态键步数」

    先用 1、2、4、8… 步指数探测，再在「最后一个包不住的」和「第一个包住的」之间二分。
    形态键最多 15 步，逐个加最多 15 次求值；指数 + 二分一般 4～6 次就够。
    返回是否包住；包住时 state["scale"] / state["bvh"] 已经是最终值。
    """
    low = state["scale"]
    high = HAND_SPHERE_SCALE_MAX_STEPS
    if low >= high:
        return False

    probe = 1
    covered_steps = None
    while True:
        candidate = min(low + probe, high)
        covered, sphere_bvh = _evaluate_candidate(sphere, state["steps"], candidate, hand_bvh)
        state["bvh"] = sphere_bvh
        if covered:
            covered_steps = candidate
            state["bvh"] = sphere_bvh
            break
        low = candidate
        if candidate >= high:
            return False
        probe *= 2

    # 二分：low 一定包不住，covered_steps 一定包得住
    while covered_steps - low > 1:
        middle = (low + covered_steps) // 2
        covered, sphere_bvh = _evaluate_candidate(sphere, state["steps"], middle, hand_bvh)
        if covered:
            covered_steps = middle
            state["bvh"] = sphere_bvh
        else:
            low = middle

    state["scale"] = covered_steps
    return True


def _grow_offset_until_covered(sphere, state, hand_bvh):
    """
    形态键也顶到上限了，才继续加大 offset（超过软上限）

    同样是先指数探测再二分，上限是 sphere.max_steps。返回是否包住。
    """
    low = state["steps"]
    high = sphere.max_steps
    if low >= high:
        return False

    probe = 1
    covered_steps = None
    while True:
        candidate = min(low + probe, high)
        covered, sphere_bvh = _evaluate_candidate(sphere, candidate, state["scale"], hand_bvh)
        state["bvh"] = sphere_bvh
        if covered:
            covered_steps = candidate
            break
        low = candidate
        if candidate >= high:
            return False
        probe *= 2

    while covered_steps - low > 1:
        middle = (low + covered_steps) // 2
        covered, sphere_bvh = _evaluate_candidate(sphere, middle, state["scale"], hand_bvh)
        if covered:
            covered_steps = middle
            state["bvh"] = sphere_bvh
        else:
            low = middle

    state["steps"] = covered_steps
    sphere.offset_minimized = True
    return True


def update_hand_spheres(spheres, frame, depsgraph=None):
    """
    逐帧调整左右手罩体，找到「刚好包住手」的 Shrinkwrap.offset，并打关键帧。

    三层调整顺序
    ------------
    1. offset（首选）：从上一帧的步数出发，在 [0, 0.015] 里 +/- 找最小可包住值
    2. 「放大」形态键：offset 顶到 0.015 还包不住，就把形态键一步 +0.01，最多到 0.15。
       先把罩体的初始面撑大，Shrinkwrap 从外面往里贴，才不会把面折进去
    3. offset 硬上限：形态键也顶到 0.15 还包不住，才继续加大 offset（最多 max_steps）

    判定标准
    --------
    罩体和手部网格做 BVH overlap（和判断手部与其他部件穿模完全一样）：
        - 有面相交：手从罩体里穿出来了，还不够大，需要继续放大；
        - 没有面相交：罩体已经包住手，可以试着缩小一点。

    幅度都用整数步数表示
    --------------------
        offset = 步数 * HAND_SPHERE_OFFSET_STEP（0.001），软上限 15 步 = 0.015
        形态键 = 步数 * HAND_SPHERE_SCALE_STEP（0.01），上限 15 步 = 0.15
    整数加减不会积累浮点误差，也方便判断上限和 0。

    怎么做到「快」
    --------------
    每次改参数都要重算一次依赖图，单次约 2.5 ms，这是整个检测里最贵的操作：
        ［形态键］-> Armature -> Shrinkwrap -> Smooth
    所以省时间就是省求值次数：

    1. 切帧后 Blender 已经按关键帧求过一次，起点参数正好等于上一帧的最终值，
       这一帧直接复用这次求值（只建 BVH，不重算依赖图）。
    2. 两只手的候选值合并成一次求值，两边共用。
    3. 「再小一步就包不住」验过一次就记下来（offset_minimized），
       之后 HAND_SPHERE_SHRINK_RECHECK_FRAMES（4）帧内不再每帧重试；
       手变大时会立刻重新往上找，手变小时最迟 4 帧后复核。
    4. 形态键 / 超过软上限的 offset 都改用「指数探测 + 二分」，
       不再一格格往上加（最多 15 步的一格格试变成 4～6 次）。

    sphere_states[sphere] 里每一项的含义
    ----------------------------------
    steps   : 当前已经接受的 offset 步数
    scale   : 当前已经接受的形态键步数（0 表示没用到形态键）
    covered : (steps, scale) 这一步是否已经包住手
    bvh     : (steps, scale) 这一步的罩体 BVH（最终碰撞检测直接用它）
    done    : 这只手的 offset 搜索是否已经结束
    inflated: 这一帧是否刚靠加大形态键包住手（是的话不用再试往回缩）

    搜索过程
    --------
    1. 用上一帧的 offset / 形态键作为起点（手部动作连续，通常离正确答案很近）。
    2. 没包住 -> offset +1；包住 -> offset -1，看能不能更小（上面第 3 条会跳过重复试探）。
    3. offset 加到软上限仍没包住 -> 形态键往上找，直到包住或到 0.15。
    4. 形态键也到上限仍没包住 -> offset 继续往上找，直到包住或到 max_steps。
    5. 上一帧用过形态键的，这一帧能缩就缩（缩一步仍包住就缩），免得一直撑大。
    最后写回 offset / 形态键，两者都打关键帧。
    返回 {罩体对象名: 当帧 BVH}，供碰撞检测直接使用。
    """
    if not spheres:
        return {}

    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    # sphere_hand_bvh_map[sphere] = 这只罩体对应的手部网格 BVH
    # （键是罩体，值是「它要包住的那只手」的 BVH）
    # 手部姿态在「调整参数」的过程中不会变，所以每帧只需要建一次；
    # 后面每个候选值都拿它和罩体 BVH 做 overlap。
    sphere_hand_bvh_map = {}

    # sphere_states[sphere] = 这只手的搜索状态，字段含义见函数开头说明。
    sphere_states = {}
    for sphere in spheres:
        eval_hand = sphere.hand_obj.evaluated_get(depsgraph)
        if not eval_hand.data.vertices:
            continue

        sphere_hand_bvh_map[sphere] = BVHTree.FromObject(eval_hand, depsgraph)
        sphere_states[sphere] = {
            # 以上一帧的最终值作为这一帧的起点：
            # offset 可能因为上一帧走投无路超过了软上限，那就从那个值接着往下试
            "steps": sphere.offset_steps,
            "scale": min(sphere.scale_steps, HAND_SPHERE_SCALE_MAX_STEPS),
            "covered": False,
            # 等第一轮评估后填入当前参数对应的 BVH
            "bvh": None,
            # 是否已经找到最终 offset
            "done": False,
            # 这一帧有没有因为包不住而加大形态键
            "inflated": False,
        }

    if not sphere_states:
        return {}

    # 什么时候该复核「offset 还能不能更小」：
    # 上一帧验证过的最小值不用每帧重试，隔几帧复核一次就够
    recheck_minimal = frame % HAND_SPHERE_SHRINK_RECHECK_FRAMES == 0

    # ------------------------------------------------------------------
    # 第一轮：判断「上一帧的值」在这一帧还能不能包住手
    # ------------------------------------------------------------------
    # 切帧时 Blender 已经按关键帧求值过一次，罩体的 offset / 形态键
    # 通常正好等于上一帧的值。如果确实相等，当前依赖图里的罩体就是
    # 我们要的几何，直接建 BVH 即可，不用再改参数、再更新一次依赖图。
    start_params_already_evaluated = all(
        abs(sphere.shrinkwrap.offset - state["steps"] * HAND_SPHERE_OFFSET_STEP) < 1e-6
        and abs(sphere.scale_value - state["scale"] * HAND_SPHERE_SCALE_STEP) < 1e-6
        for sphere, state in sphere_states.items()
    )

    if start_params_already_evaluated:
        # 复用切帧后的求值结果：只建 BVH，不触发新的依赖图更新
        sphere_bvh_map = {
            sphere: BVHTree.FromObject(
                sphere.obj.evaluated_get(depsgraph), depsgraph)
            for sphere in sphere_states
        }
    else:
        # 关键帧求值结果和上一帧的值不一致（例如插值/外插），
        # 需要显式把两只手都设回起点，再统一更新一次。
        sphere_bvh_map = _apply_sphere_settings({
            sphere: (state["steps"], state["scale"]) for sphere, state in sphere_states.items()
        })

    for sphere, state in sphere_states.items():
        # 用「罩体 BVH 和手部 BVH 有没有面相交」判断是否包住
        state["covered"] = sphere._covers_hand(
            sphere_bvh_map[sphere], sphere_hand_bvh_map[sphere])
        # 保存这一步的 BVH；如果后面不再调整，它就是最终碰撞用的 BVH
        state["bvh"] = sphere_bvh_map[sphere]

    # ------------------------------------------------------------------
    # 后续轮次：offset 在软上限内 +/- 试探；两只手共用一次更新
    # ------------------------------------------------------------------
    while True:
        # 这一轮要一起试探的新值。只收集还没结束的手，
        # 已经 done 的手不会出现在这里，也不会被改动。
        next_settings_by_sphere = {}
        for sphere, state in sphere_states.items():
            if state["done"]:
                continue

            if not state["covered"]:
                # 放大阶段：当前 offset 包不住，需要 +1 步
                if state["steps"] >= HAND_SPHERE_OFFSET_SOFT_MAX_STEPS:
                    # 到软上限还包不住：offset 先不再往上推，交给后面的形态键
                    state["done"] = True
                    continue
                next_settings_by_sphere[sphere] = (state["steps"] + 1, state["scale"])
            else:
                # 缩小阶段：当前 offset 能包住，尝试更小，看能不能更贴合
                if state["steps"] <= 0:
                    sphere.offset_minimized = True
                    state["done"] = True
                    continue
                if sphere.offset_minimized and not recheck_minimal:
                    # 上一帧刚验证过「再小一步就包不住」，这一帧先不试
                    state["done"] = True
                    continue
                if state["steps"] > HAND_SPHERE_OFFSET_SOFT_MAX_STEPS:
                    # 上一帧走投无路把 offset 推过了软上限：直接试软上限，
                    # 别一格格往回挪（那样一次要几十次求值）
                    next_settings_by_sphere[sphere] = (
                        HAND_SPHERE_OFFSET_SOFT_MAX_STEPS, state["scale"])
                else:
                    next_settings_by_sphere[sphere] = (state["steps"] - 1, state["scale"])

        if not next_settings_by_sphere:
            # 所有手都 done，本阶段搜索结束
            break

        # 把这一轮所有候选值一起写进修改器和形态键，只更新一次依赖图，
        # 然后为每个候选建 BVH。已 done 的手不会出现在这里，也不会再被改动。
        sphere_bvh_map = _apply_sphere_settings(next_settings_by_sphere)

        for sphere, (steps, _scale) in next_settings_by_sphere.items():
            state = sphere_states[sphere]
            sphere_bvh = sphere_bvh_map[sphere]
            covered = sphere._covers_hand(sphere_bvh, sphere_hand_bvh_map[sphere])

            if not state["covered"]:
                # ---------------- 放大阶段 ----------------
                # 当前没包住，这一步无论包没包住都要接受：
                # - 这一步包住了：上一档包不住，说明它就是最小值；
                # - 这一步还没包住：下一轮继续 +1。
                state["steps"] = steps
                state["covered"] = covered
                state["bvh"] = sphere_bvh
                sphere.offset_minimized = covered

                if not covered and steps >= HAND_SPHERE_OFFSET_SOFT_MAX_STEPS:
                    # 顶到软上限仍没包住，等下面用形态键接着试
                    state["done"] = True

            elif covered:
                # ---------------- 缩小试探成功 ----------------
                # 更小的 offset 仍然包住手，接受它，下一轮继续尝试更小
                state["steps"] = steps
                state["bvh"] = sphere_bvh
                sphere.offset_minimized = False

            else:
                # ---------------- 缩小试探失败 ----------------
                # 再小一步就包不住手了，说明上一步的 steps 就是最小可包住值。
                # 保留原来的 steps 和 bvh，不再调整（下面几帧也不用再试）。
                sphere.offset_minimized = True
                state["done"] = True

    # ------------------------------------------------------------------
    # 软上限内包不住的手：先加「放大」形态键，形态键也到顶了才继续加大 offset
    # ------------------------------------------------------------------
    for sphere, state in sphere_states.items():
        if state["covered"]:
            continue

        # 形态键往上找（指数探测 + 二分），offset 保持在软上限
        if sphere.scale_key is not None and _grow_scale_until_covered(
                sphere, state, sphere_hand_bvh_map[sphere]):
            state["covered"] = True
            state["inflated"] = True
            sphere.scale_minimized = True
            # 形态键变了，offset 也许还能再小一点，下一帧重新试
            sphere.offset_minimized = False
            continue

        # 形态键也到上限还包不住：这时才继续加大 offset，直到硬上限。
        # 先把形态键定在上限：offset 超过软上限只有在形态键已经到 0.15 时才成立，
        # 否则会退化成「offset 很大、形态键很小」的违规组合
        state["scale"] = HAND_SPHERE_SCALE_MAX_STEPS
        sphere.scale_minimized = True
        if _grow_offset_until_covered(sphere, state, sphere_hand_bvh_map[sphere]):
            state["covered"] = True

    # ------------------------------------------------------------------
    # 上一帧用过形态键的，这一帧能缩就缩：缩一步仍包住就接受，再试下一步
    # ------------------------------------------------------------------
    for sphere, state in sphere_states.items():
        if not state["covered"] or state["scale"] <= 0 or state["inflated"]:
            continue
        if state["steps"] > HAND_SPHERE_OFFSET_SOFT_MAX_STEPS:
            # offset 还在软上限之外时不能缩形态键：那正是它撑着才够大的，
            # 缩了就会变成「offset 超过 0.015、形态键却没到 0.15」
            continue
        if sphere.scale_minimized and not recheck_minimal:
            continue

        while state["scale"] > 0:
            covered, sphere_bvh = _evaluate_candidate(
                sphere, state["steps"], state["scale"] - 1, sphere_hand_bvh_map[sphere])
            if not covered:
                sphere.scale_minimized = True
                break
            state["scale"] -= 1
            state["bvh"] = sphere_bvh
            sphere.scale_minimized = False
        else:
            # 缩到 0 都还包住，那 0 就是最小值
            sphere.scale_minimized = True

    # ------------------------------------------------------------------
    # 写回最终值并打关键帧
    # ------------------------------------------------------------------
    result = {}
    for sphere, state in sphere_states.items():
        # 把「刚好包住」的 offset / 形态键写回，
        # 因为上面试别的值时可能把修改器停在了不对的值上
        sphere.offset_steps = state["steps"]
        sphere.shrinkwrap.offset = state["steps"] * HAND_SPHERE_OFFSET_STEP
        sphere.set_scale_steps(state["scale"])

        # 每一帧都打关键帧，之后用户拖动时间轴就能看到它们的变化
        sphere.keyframe(frame)

        # 返回当前帧最终使用的 BVH（对应 steps / scale 那一步），供碰撞检测使用
        result[sphere.obj.name] = state["bvh"]

    return result


def create_hand_spheres(context, armature, hand_objs_by_side, collection, temp_tag):
    """
    按角色静置姿态创建左右手罩体，返回 HandSphere 列表

    hand_objs_by_side 为 {侧: HAND 临时网格}，例如 {"L": HAND.L, "R": HAND.R}
    """
    # https://docs.blender.org/api/5.2/bpy.types.Armature.html#bpy.types.Armature.pose_position
    pose_position = armature.data.pose_position
    armature.data.pose_position = 'REST'
    try:
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        spheres = []
        for side in HAND_SPHERE_SIDES:
            hand_obj = hand_objs_by_side.get(side)
            if hand_obj is None:
                continue

            hand_vertex_cos = get_hand_local_cos(hand_obj, depsgraph)
            sphere = create_side_sphere(
                context, armature, hand_obj, side, collection, temp_tag, hand_vertex_cos)
            if sphere is not None:
                spheres.append(sphere)

        return spheres
    finally:
        armature.data.pose_position = pose_position
        bpy.context.view_layer.update()

"""手部罩体：穿模检测时包裹手部的罩体

罩体由「正方体 + 3 级 Catmull-Clark 表面细分」应用得到（384 面），再依次添加：
    - Armature：顶点组权重 手首.L / 手首.R，跟随临时骨架
    - Shrinkwrap：目标是对应侧的 HAND 临时网格
    - Smooth：5 次迭代、系数 1.0，把收缩后的表面抹平

创建时按角色静置姿态的手部网格确定罩体中心与初始大小；检测时逐帧调整
Shrinkwrap.offset（步长 0.001），让罩体正好包住手，并给 offset 打关键帧。

罩体放在自己的「罩体」集合里，不属于「穿模检测临时集合」：检测结束会保留下来，
「删除临时对象」也清不到它。同一个集合里还放着它专用的手部代理网格（不精简、隐藏）
和精简后的临时骨架（隐藏）——这三样都属于「罩体专用」，每次执行检测前整组重建。
"""

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..utils import *

# 一侧罩体的目标细分级别：3 级 Catmull-Clark 后约 384 面
HAND_SPHERE_SUBDIV_LEVELS = 3

# Smooth 修改器参数：5 次迭代、系数 1.0
HAND_SPHERE_SMOOTH_ITERATIONS = 5
HAND_SPHERE_SMOOTH_FACTOR = 1.0

# Shrinkwrap 偏移的单次调整幅度
HAND_SPHERE_OFFSET_STEP = 0.001

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
    return HandSphere(ball, hand_obj, side, shrinkwrap, max_steps)


class HandSphere:
    """
    一只手对应的罩体

    罩体通过 Armature 跟随手首，通过 Shrinkwrap 贴住对应手；每帧调整
    Shrinkwrap.offset 让罩体表面把所有手部顶点包在里面，并给 offset 打关键帧。
    """

    def __init__(self, obj, hand_obj, side, shrinkwrap, max_steps):
        self.obj = obj
        self.hand_obj = hand_obj
        self.side = side
        self.shrinkwrap = shrinkwrap
        self.max_steps = max_steps
        self.offset_steps = 0

    @staticmethod
    def _covers_hand(tree, hand_tree):
        """
        罩体是否包住手：直接看罩体和手有没有面相交

        和判断手部与其他部件穿模用的是同一套 BVH overlap。
        罩体整体包住手时两者表面不应相交；一旦相交，说明手从罩体里穿出来了。
        """
        return not tree.overlap(hand_tree)


def _apply_offsets(spheres_offsets):
    """
    同时设置多个罩体的 offset，只做一次依赖图更新，再为每个罩体建 BVH

    spheres_offsets 是 {HandSphere: 步数}。返回 {对象名: BVH}。
    """
    for sphere, steps in spheres_offsets.items():
        sphere.shrinkwrap.offset = steps * HAND_SPHERE_OFFSET_STEP

    # 只有罩体的修改器参数变了，依赖图里被标记的也只有罩体；取当前依赖图即可拿到新结果
    depsgraph = bpy.context.evaluated_depsgraph_get()

    return {
        sphere.obj.name: BVHTree.FromObject(
            sphere.obj.evaluated_get(depsgraph), depsgraph)
        for sphere in spheres_offsets
    }


def update_hand_spheres(spheres, frame, depsgraph=None):
    """
    逐帧调整左右手罩体，找到「刚好包住手」的最小 Shrinkwrap.offset，并打关键帧。

    判定标准
    --------
    罩体和手部网格做 BVH overlap（和判断手部与其他部件穿模完全一样）：
        - 有面相交：手从罩体里穿出来了，offset 不够，需要放大；
        - 没有面相交：罩体已经包住手，可以尝试把 offset 缩小一点。

    offset 用「步数」表示
    --------------------
    不直接存浮点 offset，而是存整数步数：
        offset = 步数 * HAND_SPHERE_OFFSET_STEP（0.001）
    这样所有加减都是整数，避免反复 +/-0.001 产生浮点误差，也方便判断上限和 0。

    为什么两只手要一起更新
    ----------------------
    每次修改 Shrinkwrap.offset 后，Blender 都要重新求值罩体的修改器栈：
        Armature -> Shrinkwrap -> Smooth
    这是整个检测里最贵的操作之一。左右手互不影响，所以每一轮把两只手
    「下一步要试的 offset」一起写进去，只更新一次依赖图、一次性建两个 BVH：
        依赖图更新次数 = max(左手调整次数, 右手调整次数)
    而不是两只手各自更新之和。

    states[sphere] 里每一项的含义
    ----------------------------
    current : 当前已经接受的步数（offset = current * 0.001）
    covered : current 这一步是否已经包住手
    tree    : current 这一步对应的罩体 BVH（最终碰撞检测直接用它）
    done    : 这只手是否已经找到最终步数，后续轮次不再动它

    搜索过程
    --------
    1. 用上一帧的步数作为起点（手部动作连续，通常离正确答案很近）。
    2. 没包住 -> 步数 +1；包住 -> 步数 -1，看能不能再小一点。
    3. 重复，直到：
        - 放大到上限仍没包住 -> 停在上限；
        - 已经缩到 0 -> 0 就是最小；
        - 缩小一步后包不住 -> 退回上一步，结束。
    最后写回 offset 并给当前帧打关键帧。
    返回 {罩体对象名: 当帧 BVH}，供碰撞检测直接使用。
    """
    if not spheres:
        return {}

    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    # hand_data[sphere] = 当前帧手部网格的 BVH。
    # 手部姿态在「调整 offset」的过程中不会变，所以每帧只需要建一次；
    # 后面每个候选 offset 都拿它和罩体 BVH 做 overlap。
    hand_data = {}

    # 每只手的搜索状态，字段含义见函数开头说明。
    states = {}
    for sphere in spheres:
        eval_hand = sphere.hand_obj.evaluated_get(depsgraph)
        if not eval_hand.data.vertices:
            continue

        # 手部 BVH：和碰撞检测一样，只看罩体与手有没有面相交
        hand_tree = BVHTree.FromObject(eval_hand, depsgraph)
        hand_data[sphere] = hand_tree
        states[sphere] = {
            # 以上一帧的最终步数作为这一帧的起点
            "current": sphere.offset_steps,
            # 还没判断这一帧的当前 offset 是否包住手
            "covered": False,
            # 等第一轮评估后填入当前 offset 对应的 BVH
            "tree": None,
            # 是否已经找到最终步数
            "done": False,
        }

    if not states:
        return {}

    # ------------------------------------------------------------------
    # 第一轮：判断「上一帧的步数」在这一帧还能不能包住手
    # ------------------------------------------------------------------
    # 切帧时 Blender 已经按关键帧求值过一次，罩体的 Shrinkwrap.offset
    # 通常正好等于上一帧的步数。如果确实相等，当前依赖图里的罩体就是
    # 我们要的几何，直接建 BVH 即可，不用再改参数、再更新一次依赖图。
    current_offsets = {sphere: state["current"] for sphere, state in states.items()}
    already_applied = all(
        abs(sphere.shrinkwrap.offset - steps * HAND_SPHERE_OFFSET_STEP) < 1e-6
        for sphere, steps in current_offsets.items()
    )

    if already_applied:
        # 复用切帧后的求值结果：只建 BVH，不触发新的依赖图更新
        trees = {
            sphere.obj.name: BVHTree.FromObject(
                sphere.obj.evaluated_get(depsgraph), depsgraph)
            for sphere in states
        }
    else:
        # 关键帧求值结果和上一帧步数不一致（例如插值/外插），
        # 需要显式把两只手都设回起点，再统一更新一次。
        trees = _apply_offsets(current_offsets)

    for sphere, state in states.items():
        hand_tree = hand_data[sphere]
        # 用「罩体 BVH 和手部 BVH 有没有面相交」判断是否包住
        state["covered"] = sphere._covers_hand(trees[sphere.obj.name], hand_tree)
        # 保存这一步的 BVH；如果后面不再调整，它就是最终碰撞用的 BVH
        state["tree"] = trees[sphere.obj.name]
        # 起点已经在上限、仍没包住：只能停在上限，结束这只手的搜索
        if not state["covered"] and state["current"] >= sphere.max_steps:
            state["done"] = True

    # ------------------------------------------------------------------
    # 后续轮次：没包住的 +1，已包住的 -1 试探；两只手共用一次更新
    # ------------------------------------------------------------------
    while True:
        # 这一轮要一起试探的新步数。只收集还没结束的手，
        # 已经 done 的手不会出现在这里，也不会被改动。
        next_offsets = {}
        for sphere, state in states.items():
            if state["done"]:
                continue

            if not state["covered"]:
                # 放大阶段：当前 offset 包不住，需要 +1 步
                if state["current"] >= sphere.max_steps:
                    # 已经到上限，无法再放大，停在上限
                    state["done"] = True
                    continue
                next_offsets[sphere] = state["current"] + 1
            else:
                # 缩小阶段：当前 offset 能包住，尝试 -1 步，看能不能更贴合
                if state["current"] <= 0:
                    # 已经是 0，最小就是 0，不需要再试
                    state["done"] = True
                    continue
                next_offsets[sphere] = state["current"] - 1

        if not next_offsets:
            # 所有手都 done，搜索结束
            break

        # 把这一轮所有候选步数一起写进修改器，只更新一次依赖图，
        # 然后为每个候选 offset 建 BVH。注意：只更新 next_offsets 里的手，
        # 已 done 的手不会出现在这里，也不会再被改动（它的 tree 已经保存好了）。
        trees = _apply_offsets(next_offsets)

        for sphere, steps in next_offsets.items():
            state = states[sphere]
            hand_tree = hand_data[sphere]
            covered = sphere._covers_hand(trees[sphere.obj.name], hand_tree)

            if not state["covered"]:
                # ---------------- 放大阶段 ----------------
                # 当前没包住，这一步无论包没包住都要接受：
                # - 这一步包住了：下一轮会进入缩小阶段，尝试 -1；
                # - 这一步还没包住：下一轮继续 +1。
                state["current"] = steps
                state["covered"] = covered
                state["tree"] = trees[sphere.obj.name]

                if not covered and steps >= sphere.max_steps:
                    # 放大到上限仍没包住，只能停在这里
                    state["done"] = True

            elif covered:
                # ---------------- 缩小试探成功 ----------------
                # 更小的 offset 仍然包住手，接受它，下一轮继续尝试更小
                state["current"] = steps
                state["tree"] = trees[sphere.obj.name]

            else:
                # ---------------- 缩小试探失败 ----------------
                # 再小一步就包不住手了，说明上一步的 current 就是最小可包住值。
                # 保留原来的 current 和 tree，不再调整。
                #
                # 注意：这时修改器里的 offset 还停在刚试过的较小值上，
                # 但下面不会再使用它的求值结果；最终写回阶段会把 offset
                # 设回 current 并打关键帧。
                state["done"] = True

    # ------------------------------------------------------------------
    # 写回最终步数并打关键帧
    # ------------------------------------------------------------------
    result = {}
    for sphere, state in states.items():
        # 把「刚好包住」的步数换算回 offset，写回修改器
        sphere.offset_steps = state["current"]
        sphere.shrinkwrap.offset = state["current"] * HAND_SPHERE_OFFSET_STEP

        # 每一帧都打关键帧，之后用户拖动时间轴就能看到 offset 的变化
        sphere.shrinkwrap.keyframe_insert(data_path="offset", frame=frame)

        # 返回当前帧最终使用的 BVH（对应 current 那一步），供碰撞检测使用
        result[sphere.obj.name] = state["tree"]

    return result


def create_hand_spheres(context, armature, hand_objs, collection, temp_tag):
    """
    按角色静置姿态创建左右手罩体，返回 HandSphere 列表

    hand_objs 为 {侧: HAND 临时网格}
    """
    # https://docs.blender.org/api/5.2/bpy.types.Armature.html#bpy.types.Armature.pose_position
    pose_position = armature.data.pose_position
    armature.data.pose_position = 'REST'
    try:
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        spheres = []
        for side in HAND_SPHERE_SIDES:
            hand_obj = hand_objs.get(side)
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

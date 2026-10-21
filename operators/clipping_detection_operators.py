import copy
import traceback
from collections import OrderedDict

import mathutils

from ..properties.clipping_detection_properties import get_ordered_detection_range, set_frame_hits, \
    clear_clipping_results, get_clipping_type_labels
from ..utils import *
from .hand_shell_operators import create_hand_spheres, update_hand_spheres, \
    HAND_SPHERE_COLLECTION_NAME

VG_NAMES = [""]
VG_PAIRS = [""]

# 身体部位与顶点组列表的映射
PART_VGS = OrderedDict({
    "TORSO": [
        '下半身', '上半身', '上半身2',
    ],
    "HAND": [
        '手首.L',
        '親指０.L', '親指１.L', '親指２.L',
        '人指１.L', '人指２.L', '人指３.L',
        '中指１.L', '中指２.L', '中指３.L',
        '薬指１.L', '薬指２.L', '薬指３.L',
        '小指１.L', '小指２.L', '小指３.L',
        '手首.R',
        '親指０.R', '親指１.R', '親指２.R',
        '人指１.R', '人指２.R', '人指３.R',
        '中指１.R', '中指２.R', '中指３.R',
        '薬指１.R', '薬指２.R', '薬指３.R',
        '小指１.R', '小指２.R', '小指３.R',
    ],
    "UPPER_ARM": [
        '腕.L', '腕捩.L', '腕捩1.L', '腕捩2.L', '腕捩3.L',
        '腕.R', '腕捩.R', '腕捩1.R', '腕捩2.R', '腕捩3.R',
    ],
    "FOREARM": [
        'ひじ.L', 'ひじ補助.L', '+ひじ補助.L', '手捩.L', '手捩1.L', '手捩2.L', '手捩3.L',
        'ひじ.R', 'ひじ補助.R', '+ひじ補助.R', '手捩.R', '手捩1.R', '手捩2.R', '手捩3.R',
    ],
    "HEAD": [
        '首', '頭',
    ],
    "LOWER_LIMBS": [
        '足.L', 'ひざ.L', '足首.L', 'つま先.L',
        '足.R', 'ひざ.R', '足首.R', 'つま先.R',
        '足D.L', 'ひざD.L', '足首D.L', '足先EX.L',
        '足D.R', 'ひざD.R', '足首D.R', '足先EX.R',
    ],
})

# 大臂被「腕捩」再分段时，'腕' 与这些捩骨骼共存，需要把 '腕' 从大臂部件里去掉
UPPER_ARM_TWIST_VGS = {
    '腕.L': ('腕捩.L', '腕捩1.L', '腕捩2.L', '腕捩3.L'),
    '腕.R': ('腕捩.R', '腕捩1.R', '腕捩2.R', '腕捩3.R'),
}


# 左右两侧的标记
SIDES = ("L", "R")

# 这些部件会按左右拆成两块代理网格：
# 罩体要以「对应那只手」作为 Shrinkwrap 目标，而「左手 vs 右手」「左小臂 vs 右小臂」
# 也必须有两块分开的网格才能测，所以手部和小臂都在这里
SIDE_SPLIT_CATEGORIES = ("HAND", "FOREARM")


def get_side_vg_names(category):
    """
    把某个部件的顶点组名按左右拆开，供左右分开的临时网格使用

    例：FOREARM -> {"L": {"ひじ.L", "手捩.L", ...}, "R": {"ひじ.R", ...}}
    """
    return {
        side: {name for name in PART_VGS[category] if name.endswith("." + side)}
        for side in SIDES
    }


def split_vertices_by_side(mesh_obj, vertices, side_vg_names):
    """
    按顶点组权重把某个部件的顶点分成左右两半

    side_vg_names 是 {侧: 顶点组名集合}。
    返回 {侧: 顶点索引列表}；每个顶点只归到权重更大的那一侧。
    """
    index_to_name = {vertex_group.index: vertex_group.name for vertex_group in mesh_obj.vertex_groups}
    name_to_side = {}
    for side, names in side_vg_names.items():
        for name in names:
            name_to_side[name] = side

    result = {side: [] for side in side_vg_names}
    for vertex_index in vertices:
        vertex = mesh_obj.data.vertices[vertex_index]
        weights = {}
        for element in vertex.groups:
            side = name_to_side.get(index_to_name.get(element.group))
            if side is None:
                continue
            weights[side] = weights.get(side, 0.0) + element.weight

        if not weights:
            continue

        side = max(weights, key=weights.get)
        if weights[side] > 0.0:
            result[side].append(vertex_index)

    return result


def get_part_objects_for_category(part_objs, category):
    """
    部件名 -> 实际参与检测的临时网格列表

    HAND / FOREARM 已按左右拆成 X.L / X.R，一个「手部-躯干」会生成两条检测对；
    「手部-左右手」这类左右互检也靠这两块网格。
    """
    if category in SIDE_SPLIT_CATEGORIES:
        return [
            obj for obj in (part_objs.get(f"{category}.{side}") for side in SIDES)
            if obj is not None
        ]

    obj = part_objs.get(category)
    return [obj] if obj is not None else []


def build_detection_pairs(part_objs, detection_range):
    """
    检测范围 -> 实际参与碰撞的 [(网格A, 网格B), ...] 和 配对名

    一个逻辑部件对可能展开成多条实际检测对（左右手各一条）；
    反过来，「手部-左右手」这种同一部件的左右互检，左右两边会各遍历到一次，
    统一成同一个键，既不会自己跟自己比，也不会重复测一遍。
    """
    pair_names = {}
    for item in detection_range:
        a, b = item.split("-")
        same_part = a == b
        for obj_a in get_part_objects_for_category(part_objs, a):
            for obj_b in get_part_objects_for_category(part_objs, b):
                # 同一块代理网格自己跟自己比没有意义
                if obj_a is obj_b:
                    continue
                if same_part:
                    # 按名字排序，让 (左, 右) 和 (右, 左) 落到同一个键上
                    key = tuple(sorted((obj_a, obj_b), key=lambda obj: obj.name))
                else:
                    key = (obj_a, obj_b)
                pair_names[key] = item

    return list(pair_names.keys()), pair_names


# 躯干骨骼链的两端：从「首」往上一直找到「上半身」
SPINE_NECK_BONE_NAME = '首'
SPINE_ROOT_BONE_NAME = '上半身'

TMP_ANALYSIS_COLLECTION_NAME = "穿模检测临时集合"

# 临时对象的标记属性
TMP_OBJECT_TAG = "mmd_motion_tools_tmp"

# 勾了手部罩体时，这两块手部代理网格属于「罩体专用」：不精简、建在「罩体」集合里、
# 隐藏，检测结束也不跟着临时对象一起删（下次执行开始时，和临时集合一起清掉再重建）
SPHERE_PARTS = tuple(f"HAND.{side}" for side in SIDES)

# 检测进度：检测过程中会切到临时场景，scene 上的属性那时读的是另一个场景，
# 所以进度放在模块级，面板直接从 get_detection_progress() 取
_DETECTION_PROGRESS = {
    "active": False,
    "factor": 0.0,
    "text": "",
}

# 初始化（复制临时对象、划分部件）统一用这一句，不再细分步骤
INIT_PROGRESS_TEXT = "初始化中..."

# 每次 modal 事件最多推进的帧数：单帧太慢时不至于把界面卡死，
# 正常情况下 1% 远小于这个值，进度还是每 1% 刷新一次
MAX_FRAMES_PER_STEP = 100

STAGE_INIT = "init"
STAGE_DETECT = "detect"


def get_detection_progress():
    """当前检测进度（面板绘制用）"""
    return _DETECTION_PROGRESS


def set_detection_progress(active, factor=0.0, text=""):
    """更新检测进度；active=False 表示结束，面板不再画进度条"""
    _DETECTION_PROGRESS["active"] = bool(active)
    _DETECTION_PROGRESS["factor"] = min(max(float(factor), 0.0), 1.0)
    _DETECTION_PROGRESS["text"] = text


def tag_redraw(context):
    """标记 3D 视图重画：进度条在侧边栏里，不主动重绘就要等下次界面刷新"""
    window = getattr(context, "window", None)
    if window is None or window.screen is None:
        return
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def remove_redundant_upper_arm_vgs(part_vgs, vertex_group_names):
    """
    大臂部件里有 '腕.L' 且同时有任意一个「腕捩」时，把 '腕.L' 去掉（右臂同理，左右各判各的）

    对于包含次标准骨骼的大多数模型，这样可以减少肩部对检测结果的干扰。
    对于不包含次标准骨骼的模型，则不做额外适配，由用户自行从检测范围中移除大臂相关项。
    """
    vgs = part_vgs.get("UPPER_ARM")
    if not vgs:
        return

    for arm_name, twist_names in UPPER_ARM_TWIST_VGS.items():
        if arm_name not in vertex_group_names:
            continue
        if any(name in vertex_group_names for name in twist_names):
            vgs.remove(arm_name)


def get_spine_bone_names(armature):
    """取「首」往上接到「上半身」的那一串骨骼名"""
    bones = armature.data.bones
    neck = bones.get(SPINE_NECK_BONE_NAME)
    if neck is None:
        return []

    names = []
    bone = neck.parent
    while bone is not None:
        names.append(bone.name)
        # 只有遇到“上半身”才会返回
        if bone.name == SPINE_ROOT_BONE_NAME:
            return names
        bone = bone.parent

    return []


def add_spine_bones_to_torso(part_vgs, armature):
    """把「首」到「上半身」之间的骨骼补进 TORSO，TORSO 里已有的不重复加"""
    torso = part_vgs.get("TORSO")
    if not torso:
        return

    for name in get_spine_bone_names(armature):
        if name not in torso:
            torso.append(name)


def clear_previous_temp_objects():
    """
    清掉上一次执行留下的所有临时对象

    - 「穿模检测临时集合」：上一次的部件代理网格
    - 「罩体」集合：罩体专用对象（HAND 代理网格 / 临时骨架 / 罩体）

    它们都是临时对象、不是 MMD 模型的一部分，所以必须在收集 objects 之前删干净，
    否则会被当成模型的网格算进去（勾不勾罩体、勾不勾删除临时对象都一样）。
    集合清空后一并删掉，需要时再新建——每次执行都是重新生成。
    """
    for collection_name in (TMP_ANALYSIS_COLLECTION_NAME, HAND_SPHERE_COLLECTION_NAME):
        collection = bpy.data.collections.get(collection_name)
        if collection is not None:
            remove_part_meshes(collection, remove_empty_collection=True)


class ClippingDetectionOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.detect_clipping"
    bl_label = "执行"
    bl_description = "检测网格对象之间的穿模情况"
    bl_options = {'REGISTER', 'UNDO'}

    def check_props(self, context):
        # 上一次执行留下的临时对象（临时集合 + 罩体集合）在这里一起清掉：
        # 删除时机一致——每次执行的开始，并且早于下面收集 objects，
        # 不然它们会被当成模型的网格算进去
        clear_previous_temp_objects()

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

        pmx_objects = find_pmx_objects(armature)
        # 只有在视图里真正显示出来的网格才参与检测，部分素体类模型可能含有默认服装，直接让用户删除的话不太合理。
        objects = [pmx_object for pmx_object in pmx_objects if is_visible_in_viewport(pmx_object)]
        if not objects:
            if pmx_objects:
                self.report({'ERROR'}, '请确保网格在3D视图中可见！')
            else:
                self.report({'ERROR'}, '未检测到网格！')
            return None

        # 没有骨架修改器的网格不会跟随动作，检测结果没有意义
        if not any(modifier.type == 'ARMATURE' for mesh_obj in objects for modifier in mesh_obj.modifiers):
            self.report({'ERROR'}, '网格未绑定骨架修改器！')
            return None

        # 顶点组是划分身体部件的依据，一个都没有就分不出任何部件
        if not any(mesh_obj.vertex_groups for mesh_obj in objects):
            self.report({'ERROR'}, '网格不存在顶点组！')
            return None

        props = context.scene.mmd_motion_tools_detect_clipping
        if not props.detection_range:
            self.report({'ERROR'}, '请至少选择一项检测范围！')
            return None

        return armature, objects

    def execute(self, context):
        result = self.check_props(context)
        if result is None:
            return {'CANCELLED'}

        self.armature, self.objects = result
        self.props = context.scene.mmd_motion_tools_detect_clipping
        self.original_scene = context.scene
        self.start_time = time.perf_counter()

        # 阶段状态
        self.stage = STAGE_INIT
        self.generator = None
        self.percent = -1
        # 每 1% 对应的帧数，start_detection 里按帧范围算
        self.frames_per_percent = 1
        self.merged = {}
        self.frame_hits = {}
        self.tmp_col = None
        self.tmp_obj = None
        self.tmp_armature = None
        self.part_objs = {}
        self.part_vgs = {}
        self.hand_spheres = []
        # 罩体专用部分：集合和要单独放的部件名（勾了手部罩体时才有值）
        self.sphere_col = None
        self.sphere_parts = ()
        # 执行前已存在的网格/形态键数据块，清理时只动这次新建的
        self.mesh_pointers_before = None
        self.key_pointers_before = None
        self.tmp_scene = None
        self.timer = None
        self.flush_count = 0
        # 进入临时场景前的着色方式（area 指针 -> 'SOLID'/'MATERIAL'/...）
        self.shading_before = {}

        # 记录选择状态
        view_layer = context.view_layer
        self.view_layer = view_layer
        self.selected_before = [obj for obj in view_layer.objects if obj.select_get()]
        self.active_before = view_layer.objects.active

        set_detection_progress(True, 0.0, INIT_PROGRESS_TEXT)

        # 后台（-b）没有事件循环，modal 永远不会被驱动，直接同步跑完
        if bpy.app.background:
            try:
                self.run_sync(context)
            except Exception as error:
                self.abort(context)
                traceback.print_exc()
                self.report({'ERROR'}, f'检测失败：{error}')
                return {'CANCELLED'}
            return {'FINISHED'}

        # 让出主线程，靠 TIMER 事件分片推进；先多等两帧，进度条才有机会画出来
        self.timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # 只处理定时器事件：检测途中用户的操作一律忽略，避免中途改坏临时数据
        if event.type != 'TIMER':
            # 例外：按 ESC 中止，先把场景和临时数据收拾干净再退出
            if event.type == 'ESC':
                self.abort(context)
                self.report({'INFO'}, '已取消检测')
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}

        if self.flush_count < 2:
            self.flush_count += 1
            tag_redraw(context)
            return {'RUNNING_MODAL'}

        try:
            if not self.step(context):
                tag_redraw(context)
                return {'RUNNING_MODAL'}
            self.finish(context)
        except Exception as error:
            # 出错也要把场景、临时数据和进度条收拾干净，否则文件会留下半截状态
            self.abort(context)
            traceback.print_exc()
            self.report({'ERROR'}, f'检测失败：{error}')
            return {'CANCELLED'}

        return {'FINISHED'}

    def run_sync(self, context):
        """同步跑完所有阶段（后台测试用）"""
        while not self.step(context):
            pass
        self.finish(context)

    def step(self, context):
        """
        推进一个阶段，全部完成时返回 True

        每个阶段只做一块活：modal 每次 TIMER 只调用一次，Blender 才有机会重绘界面
        """
        if self.stage == STAGE_INIT:
            # 复制临时对象和划分部件放在同一批里做完，即「初始化中...」
            self.init_temp_objects(context)
            self.stage = STAGE_DETECT
            return False

        return self.step_detection(context)

    def init_temp_objects(self, context):
        """初始化：复制并合并模型网格，再按顶点组划分出各部件代理网格"""
        tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
        # 集合被隐藏/排除时里面的对象选不中，join、复制修改器都会 poll 失败，先恢复显示
        show_collection(tmp_col)
        # 记录执行前已有的网格、形态键数据块：后面清理临时对象时，只清理本次新建的，
        # 不会误删用户自己留在文件里的孤立数据
        self.mesh_pointers_before = {mesh.as_pointer() for mesh in bpy.data.meshes}
        self.key_pointers_before = {key.as_pointer() for key in bpy.data.shape_keys}

        self.tmp_col = tmp_col

        # 勾了手部罩体时，罩体专用对象（HAND 代理网格 / 临时骨架 / 罩体）都放「罩体」集合里：
        # 它们在检测结束后要留着，所以不能混进临时集合被「删除临时对象」清掉
        if self.props.use_hand_sphere:
            self.sphere_col = get_collection(HAND_SPHERE_COLLECTION_NAME)
            show_collection(self.sphere_col)
            self.sphere_parts = SPHERE_PARTS

        self.tmp_obj = create_tmp_obj(context, self.objects, tmp_col)
        self.part_objs = self.create_part_objects()

        # create_part_meshes 最后会把合并出来的临时网格删掉，这里别留悬空引用
        self.tmp_obj = None

        # 临时网格改用精简过的临时骨架：原骨架里大部分骨骼跟检测无关。
        # 勾了罩体时骨架放进「罩体」集合：罩体要一直用它，检测结束也不删，只在里面隐藏
        part_objs = list(self.part_objs.values())
        armature_col = self.sphere_col or tmp_col
        self.tmp_armature = create_tmp_armature(
            context, self.armature, armature_col, part_objs, self.part_vgs)
        repoint_armature_modifiers(part_objs, self.tmp_armature)

        # 手部罩体：按静置姿态的手部代理网格分别创建左右罩体（HAND 代理网格已经在
        # 「罩体」集合里。不精 HAND 代理，简罩体才能贴得准）
        if self.sphere_col is not None:
            hand_objs = {
                side: self.part_objs.get(f"HAND.{side}")
                for side in SIDES
            }
            self.hand_spheres = create_hand_spheres(
                context, self.tmp_armature, hand_objs, self.sphere_col, TMP_OBJECT_TAG)
            if not self.hand_spheres:
                # 依然放行
                self.report({'WARNING'}, '未生成手部罩体：没有找到手部顶点或手部骨骼')

    def create_part_objects(self):
        """按顶点组划分身体部件，生成各部件代理网格，返回 {部件: 代理网格}"""
        part_vgs = copy.deepcopy(PART_VGS)
        # 大臂有「腕捩」分段时，'腕' 和分段重复，从大臂部件里去掉
        remove_redundant_upper_arm_vgs(part_vgs, {vg.name for vg in self.tmp_obj.vertex_groups})
        # 上半身被拆成多段（上半身2、上半身3等）时，把「首」到「上半身」的骨骼补进 TORSO
        add_spine_bones_to_torso(part_vgs, self.armature)
        part_vgs["BREAST"] = [bb.name for bb in get_breast_bones(self.armature)]
        # 自定义拓展：把额外指定的骨骼（顶点组）当作独立部件 CUSTOM 参与检测
        custom_vgs = split_names(self.props.custom_bones_str)
        if custom_vgs:
            part_vgs["CUSTOM"] = custom_vgs

        # 临时骨架判断「哪些骨骼有用」时要用这份定义
        self.part_vgs = part_vgs

        part_vertices = classify_vertices(self.tmp_obj, part_vgs)

        # 手部、小臂再按左右拆成两块：
        # 罩体需要以「对应那只手」作为 Shrinkwrap 目标，
        # 「左手 vs 右手」「左小臂 vs 右小臂」也得两块网格才测得了
        for category in SIDE_SPLIT_CATEGORIES:
            vertices = part_vertices.pop(category, [])
            if not vertices:
                continue
            side_vertices = split_vertices_by_side(self.tmp_obj, vertices, get_side_vg_names(category))
            for side in SIDES:
                if side_vertices.get(side):
                    part_vertices[f"{category}.{side}"] = side_vertices[side]

        return create_part_meshes(
            self.props, self.tmp_obj, part_vertices, self.tmp_col,
            sphere_parts=self.sphere_parts, sphere_col=self.sphere_col)

    def start_detection(self, context):
        """切到空白场景并建好逐帧生成器（不动原场景，避免依赖图整体重算）"""
        detection_range = get_ordered_detection_range(self.props)
        pairs, pair_key_map = build_detection_pairs(self.part_objs, detection_range)

        # 帧范围要在切换场景前读，换场景后读到的会是新场景的默认值
        frame_start = self.original_scene.frame_start
        frame_end = self.original_scene.frame_end
        self.total_frames = frame_end - frame_start + 1
        # 每 1% 对应多少帧（至少 1 帧），后面用取模判断什么时候刷新进度
        self.frames_per_percent = max(self.total_frames // 100, 1)

        # 新建空白场景，避免 Dependency graph 计算
        bpy.ops.scene.new(type='NEW')
        self.tmp_scene = bpy.context.scene
        self.sync_scene_range(self.original_scene, self.tmp_scene)
        # 跳过来之后切成实体模式
        self.set_solid_shading(context)
        # 恢复集合及内部网格的显示
        get_collection(TMP_ANALYSIS_COLLECTION_NAME)
        # 罩体在自己的「罩体」集合里，新场景里也要链一份，否则依赖图求值不到它
        if self.sphere_col is not None:
            get_collection(HAND_SPHERE_COLLECTION_NAME)
            # hide_set 是按视图层记的：罩体专用的手部代理网格在新场景里再藏一次
            for part in self.sphere_parts:
                part_obj = self.part_objs.get(part)
                if part_obj is not None:
                    part_obj.hide_set(True)
        # hide_set 是按视图层生效的，新场景的视图层里再藏一次
        self.tmp_armature.hide_set(True)

        # 检测期间不需要任何物体被选中
        for obj in context.view_layer.objects:
            try:
                if obj.select_get():
                    obj.select_set(False)
            except ReferenceError:
                continue

        self.generator = iter_detect_collisions(
            pairs, frame_start, frame_end, pair_names=pair_key_map,
            hand_spheres=self.hand_spheres)

        # 先亮一个 0%，让用户看到「已经开始了」，而不是停在初始化那一条上
        self.percent = 0
        set_detection_progress(True, 0.0, f"检测中 0%（0/{self.total_frames} 帧）")

    @classmethod
    def sync_scene_range(cls, source, target):
        """临时场景的时间范围和面板参数跟原场景保持一致"""
        target.name = "穿模检测临时场景"
        target.frame_start = source.frame_start
        target.frame_end = source.frame_end

        source_props = source.mmd_motion_tools_detect_clipping
        target_props = target.mmd_motion_tools_detect_clipping

        target_props.proxy_accuracy = source_props.proxy_accuracy
        target_props.show_detection_range = source_props.show_detection_range
        target_props.remove_tmp = source_props.remove_tmp
        target_props.use_hand_sphere = source_props.use_hand_sphere
        target_props.result_list_mode = source_props.result_list_mode

        # custom_bones_str 的 update 会自动勾上「自定义拓展」那几项，所以它先写；
        target_props.custom_bones_str = source_props.custom_bones_str
        target_props.detection_range = source_props.detection_range

    def step_detection(self, context):
        """逐帧推进检测，跑完返回 True"""
        if self.generator is None:
            self.start_detection(context)

        processed = 0

        while True:
            try:
                index, total = next(self.generator)
            except StopIteration as stop:
                # 生成器正常结束时会 return (merged, frame_hits)
                self.merged, self.frame_hits = stop.value or ({}, {})
                return True

            processed += 1

            # 取模判断：走到「1% 的整数倍」或最后一帧才交还控制权刷新进度，
            # 其余帧继续埋头干，免得每帧都来回切一次
            if index % self.frames_per_percent and index != total:
                # 兜底：单帧再慢也不能一直占着主线程
                if processed >= MAX_FRAMES_PER_STEP:
                    return False
                continue

            percent = round(index / total * 100)
            if percent == self.percent:
                continue

            self.percent = percent
            # 进度条只反映检测进度：已完成帧数占总帧数的比例，初始化不占份额
            set_detection_progress(
                True,
                percent / 100,
                f"检测中 {percent}%（{index}/{total} 帧）")
            return False

    def finish(self, context):
        """写入结果并收拾临时场景、临时对象"""
        if self.timer is not None:
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None

        props = self.props
        merged = self.merged

        # 清掉上一轮结果，同时把结果列表切回「预览模式」：
        props.clippings.clear()
        props.result_list_mode = 'SCROLL'

        total_frames = 0
        for (seg_start, seg_end), names in merged.items():
            item = props.clippings.add()
            item.start = seg_start
            item.end = seg_end
            item.types = ", ".join(names)
            total_frames += seg_end - seg_start + 1

        # 逐帧结果也存一份，列表可以按当前帧显示更精确的穿模类型
        set_frame_hits(props, self.frame_hits)

        self.restore_scene(context)

        # 临时部件代理网格按开关删除；中间产生的孤立数据块每次都要清，
        # 因为代理网格里已经不含形态键，留着也不会被用到
        self.clean_tmp_data(remove_objects=props.remove_tmp)

        # 恢复选择状态
        select_only_objects(context, self.selected_before)
        if self.active_before is not None:
            self.view_layer.objects.active = self.active_before

        set_detection_progress(False)

        if not merged:
            self.report({'INFO'}, '未检测到穿模!')
        else:
            self.report({'INFO'}, f'检测到 {len(merged)} 段穿模，共 {total_frames} 帧')

        print(f"总耗时: {time.perf_counter() - self.start_time:.4f} 秒")

    def restore_scene(self, context):
        """切回原场景、恢复着色方式，并删除临时场景"""
        if self.tmp_scene is None:
            return
        if bpy.context.window is not None:
            bpy.context.window.scene = self.original_scene
        bpy.data.scenes.remove(self.tmp_scene)
        self.tmp_scene = None
        self.restore_shading(context)

    @staticmethod
    def view3d_areas(context):
        """当前窗口里的所有 3D 视图区域（后台也有，只是不会真的绘制）"""
        window = getattr(context, "window", None)
        if window is None or window.screen is None:
            return []
        return [area for area in window.screen.areas if area.type == 'VIEW_3D']

    def set_solid_shading(self, context):
        """把 3D 视图切成实体模式，并记下原来的着色方式"""
        self.shading_before = {}
        for area in self.view3d_areas(context):
            shading = area.spaces.active.shading
            self.shading_before[area.as_pointer()] = shading.type
            shading.type = 'SOLID'

    def restore_shading(self, context):
        """把进入临时场景前的着色方式恢复回去"""
        if not self.shading_before:
            return
        for area in self.view3d_areas(context):
            previous = self.shading_before.get(area.as_pointer())
            if previous is not None:
                area.spaces.active.shading.type = previous
        self.shading_before = {}

    def clean_tmp_data(self, remove_objects):
        """
        收拾临时数据

        remove_objects=True 时删掉本次建的临时对象（合并网格 + 各部件代理）和空掉的临时集合；
        无论哪种情况都清掉这次新产生、已经没人用的孤立网格/形态键数据块。
        """
        if remove_objects and self.tmp_col is not None:
            temp_objs = [
                part_obj for part, part_obj in self.part_objs.items()
                if part not in self.sphere_parts
            ]
            remove_part_meshes(self.tmp_col, temp_objs, True)
        purge_temp_datablocks(self.mesh_pointers_before, self.key_pointers_before)

    def abort(self, context):
        """取消（ESC）或异常时收拾现场"""
        try:
            if self.timer is not None:
                context.window_manager.event_timer_remove(self.timer)
                self.timer = None
            self.restore_scene(context)
            self.clean_tmp_data(remove_objects=True)
            if self.view_layer is not None:
                select_only_objects(context, self.selected_before)
                if self.active_before is not None:
                    self.view_layer.objects.active = self.active_before
        finally:
            set_detection_progress(False)


class ResetDetectionRangeOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.reset_detection_range"
    bl_label = "重置检测范围"
    bl_description = "把检测范围恢复为默认值"
    bl_options = {'UNDO'}

    def execute(self, context):
        props = context.scene.mmd_motion_tools_detect_clipping
        # 默认值从 RNA 里读，和属性定义里的 default 始终一致
        props.detection_range = set(props.bl_rna.properties["detection_range"].default_flag)
        return {'FINISHED'}


class ClearClippingOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.clear_clipping"
    bl_label = "清除"
    bl_description = "清除穿模检测结果"
    bl_options = {'UNDO'}

    def execute(self, context):
        props = context.scene.mmd_motion_tools_detect_clipping
        clear_clipping_results(props)
        return {'FINISHED'}


class ShowClippingTypeOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.show_clipping_type"
    bl_label = "查看穿模类型"
    bl_description = "显示当前帧的穿模类型"
    bl_options = {'INTERNAL'}

    frame: bpy.props.IntProperty()
    # 这一帧命中的部件对，多个用 ", " 分隔
    types: bpy.props.StringProperty()

    def execute(self, context):
        props = context.scene.mmd_motion_tools_detect_clipping
        labels = get_clipping_type_labels(props, self.types)
        if not labels:
            return {'CANCELLED'}

        # 状态栏 / 信息编辑器里提示，比弹窗轻，也不占额外交互
        self.report({'INFO'}, f"穿模类型：" + "、".join(labels))
        return {'FINISHED'}


def apply_shape_key_mix(obj):
    """
    把当前形态键的混合结果烘焙进网格

    核心逻辑：
    result = basis.co
    for k in key_blocks[1:]:
        if k.mute or abs(k.value) < 1e-8: continue
        result += (k.co - k.relative_key.co) * k.value
    """
    mesh = obj.data
    shape_keys = mesh.shape_keys
    if shape_keys is None:
        return

    # 延迟导入，避免拖慢插件加载速度
    import numpy as np

    key_blocks = list(shape_keys.key_blocks)
    basis = key_blocks[0]  # 第 0 个永远是 Basis（基准形态）
    vertex_count = len(mesh.vertices)
    # 每个顶点有 x/y/z 三个坐标，所以统一按「顶点数 行 × 3 列」的二维数组看待
    shape = (vertex_count, 3)

    # ------------------------------------------------------------------
    # 1. 取出基准坐标
    # ------------------------------------------------------------------
    # np.empty(n)：申请一块能装 n 个数的内存，**但不清零**（内容随机）。
    # 比 np.zeros 稍快，因为反正下一步就会把它整个覆盖掉。
    # 长度写 vertex_count * 3（扁平的一维），因为 Blender 的 foreach_get
    # 只认扁平数组——它眼里就是一串紧密排列的浮点数。
    # dtype=np.float32 很重要：Blender 内部就用单精度存坐标，
    # 用 float32 才能和官方算子的结果逐位相同（用 float64 会有微小差异）。
    base = np.empty(vertex_count * 3, dtype=np.float32)

    # foreach_get("co", base)：把基准形态键的全部顶点坐标一次性拷贝进 base。
    # 这是 Blender 提供的批量读写接口，相当于 C 层的 memcpy，一次调用搬完整个数组。
    # 换成 Python 写法 [tuple(p.co) for p in basis.data] 会慢几十倍，
    # 因为要为每个坐标创建一个 Python 对象。
    basis.data.foreach_get("co", base)

    # reshape：只是「换个看法」，把同一块内存按每 3 个数一组重新解释成二维。
    # 不复制数据、几乎零开销。之后 base[i] 就是第 i 个顶点的 (x, y, z)。
    base = base.reshape(shape)

    # ------------------------------------------------------------------
    # 2. 逐个形态键累加  delta * value
    # ------------------------------------------------------------------
    # .copy() 才是真正的复制。mixed 从基准坐标出发，后面往上累加 delta。
    # 不能写成 mixed = base —— 那样 mixed 和 base 是同一块内存，改 mixed 会把基准也改掉。
    mixed = base.copy()

    # 复用同一块缓冲区轮流装每个形态键的坐标，避免在循环里反复申请/释放内存
    buffer = np.empty(vertex_count * 3, dtype=np.float32)
    relative_buffer = None  # 只有遇到 relative_key 不是基准时才需要，懒申请
    # https://docs.blender.org/api/current/bpy.types.bpy_struct.html#bpy.types.bpy_struct.as_pointer
    # as_pointer() 返回这个形态键在内存中的地址，用来判断两个形态键是不是同一个。
    # 不能用 is 或 == 比较，因为每次访问 .relative_key 拿到的都是新的 Python 包装对象。
    basis_pointer = basis.as_pointer()

    for key_block in key_blocks[1:]:  # 跳过第 0 个（Basis 自己）
        value = key_block.value
        # 与 Blender 的形态键求值保持一致：静音或形态键数值为 0 的形态键不参与混合
        if abs(value) < 1e-8 or getattr(key_block, "mute", False):
            continue

        # 取出这个形态键的整套坐标（同样是一次 memcpy），再按 (顶点数, 3) 看待
        key_block.data.foreach_get("co", buffer)
        current = buffer.reshape(shape)

        relative = key_block.relative_key
        if relative is None or relative.as_pointer() == basis_pointer:
            # 绝大多数情况：relative_key 就是 Basis，直接相对基准做差
            # current - base 是「逐元素相减」：numpy 会把全部顶点的 x/y/z
            # 一次性算完（底层是 C 循环），不需要我们写 Python for 循环
            delta = current - base
        else:
            # 少数情况：relative_key 指向另一个形态键，那就相对那一个做差
            if relative_buffer is None:
                relative_buffer = np.empty(vertex_count * 3, dtype=np.float32)
            relative.data.foreach_get("co", relative_buffer)
            delta = current - relative_buffer.reshape(shape)

        # delta * value：每个元素都乘同一个标量，numpy 会自动「广播」，
        # 不需要写循环。
        # mixed += ...：原地累加，不产生新数组，省内存也省时间。
        mixed += delta * value

    # ------------------------------------------------------------------
    # 3. 写回网格，然后删除形态键
    # ------------------------------------------------------------------
    # reshape(-1) 把二维数组重新摊平成一维（foreach_set 同样只认扁平数组）；
    # np.ascontiguousarray 保证这些数在内存里是连续排列的（当前本来就是，
    # 加上是为了防止以后改动破坏这个前提，导致 foreach_set 读错）。
    flat = np.ascontiguousarray(mixed.reshape(-1))

    # 写回基准形态键
    basis.data.foreach_set("co", flat)
    # 同时也要写回网格本体。mesh.vertices 里存的是「基准形状」的坐标，
    # 而下面 shape_key_clear() 删掉形态键之后，网格显示的就是 mesh.vertices。
    # 只写其中一处的话，形态键一删，形状就会弹回原来的基准。
    mesh.vertices.foreach_set("co", flat)

    # 删除全部形态键
    obj.shape_key_clear()


# 找「整条骨链」的起点：这两根骨骼的兄弟骨骼通常是 IK 骨骼
CHAIN_ROOT_NAMES = ("全ての親", "センター")

# 名字里带这些标记的骨骼即使没有权重也保留（和「付与親」相关）
KEEP_BONE_KEYWORDS = ("_dummy_", "_shadow_")


def get_weighted_bone_names(mesh_objects):
    """临时网格上真正带权重的骨骼名（哪怕权重很小也算）"""
    names = set()
    for obj in mesh_objects:
        group_names = {vertex_group.index: vertex_group.name for vertex_group in obj.vertex_groups}
        for vertex in obj.data.vertices:
            for element in vertex.groups:
                if element.weight > 0.0:
                    names.add(group_names.get(element.group))
    names.discard(None)
    return names


def get_ancestor_bone_names(armature, bone_names):
    """这些骨骼的所有父级 / 祖先骨骼"""
    names = set()
    for name in bone_names:
        bone = armature.data.bones.get(name)
        while bone is not None:
            names.add(bone.name)
            bone = bone.parent
    return names


def get_sibling_chain_bone_names(armature, root_names=CHAIN_ROOT_NAMES):
    """
    「全ての親」「センター」的兄弟骨骼，以及它们整条骨链

    这些兄弟骨骼一般是 IK 骨（足IK 之类）：不带权重但驱动别的骨骼，删掉姿态就变了。
    """
    bones = armature.data.bones
    names = set()

    for root_name in root_names:
        root = bones.get(root_name)
        if root is None:
            continue

        if root.parent is not None:
            siblings = [child for child in root.parent.children if child.name != root.name]
        else:
            # 根骨骼没有父级时，和它平级的只有其它顶层骨骼
            siblings = [bone for bone in bones if bone.parent is None and bone.name != root.name]

        for sibling in siblings:
            stack = [sibling]
            while stack:
                bone = stack.pop()
                if bone.name in names:
                    continue
                names.add(bone.name)
                stack.extend(bone.children)

    return names


def get_useful_bone_names(armature, mesh_objects, part_vgs):
    """
    临时骨架要保留的骨骼

    1. 各部件定义里的顶点组名对应的骨骼（顶点组名就是骨骼名）
    2. 这些骨骼的全部父级 / 祖先
    3. 「全ての親」「センター」的兄弟骨骼的整条骨链（IK 骨骼）
    4. 名字含 _dummy_ / _shadow_ 的骨骼（和「付与親」相关）
    5. 其它在临时网格上带权重的骨骼（权重很小也算）
    """
    bone_names = {name for names in part_vgs.values() for name in names}
    bone_names |= get_weighted_bone_names(mesh_objects)
    bone_names |= {
        bone.name for bone in armature.data.bones
        if any(keyword in bone.name for keyword in KEEP_BONE_KEYWORDS)
    }
    bone_names |= get_sibling_chain_bone_names(armature)

    # 祖先必须一起保留，否则骨链会断
    return bone_names | get_ancestor_bone_names(armature, bone_names)


def retarget_armature_self_references(tmp_armature, source_armature):
    """把约束里指向原骨架的引用改成指向临时骨架"""
    for pose_bone in tmp_armature.pose.bones:
        for constraint in pose_bone.constraints:
            if getattr(constraint, "target", None) == source_armature:
                constraint.target = tmp_armature


def create_tmp_armature(context, armature, tmp_col, mesh_objects, part_vgs):
    """
    复制一份骨架作为临时对象的目标骨架，并删掉用不到的骨骼

    保留 / 删除的骨骼数记在临时骨架的自定义属性上，方便排查。
    """
    tmp_armature = armature.copy()
    tmp_armature.data = armature.data.copy()
    tmp_armature.name = f"{armature.name}_tmp"
    tmp_armature.data.name = f"{armature.data.name}_tmp"
    tmp_armature[TMP_OBJECT_TAG] = True

    # 不挂在原模型的层级下，只要世界矩阵一致就行
    tmp_armature.parent = None
    tmp_armature.matrix_world = armature.matrix_world.copy()
    tmp_col.objects.link(tmp_armature)
    retarget_armature_self_references(tmp_armature, armature)

    useful_names = get_useful_bone_names(armature, mesh_objects, part_vgs)

    ensure_object_mode(context)
    select_only_objects(context, [tmp_armature])
    context.view_layer.objects.active = tmp_armature
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = tmp_armature.data.edit_bones
    removed = 0
    for edit_bone in list(edit_bones):
        if edit_bone.name in useful_names:
            continue
        edit_bones.remove(edit_bone)
        removed += 1
    # 必须在编辑模式里数：切回物体模式后 edit_bones 就空了
    kept = len(edit_bones)
    bpy.ops.object.mode_set(mode='OBJECT')

    tmp_armature["kept_bones"] = kept
    tmp_armature["removed_bones"] = removed

    # 隐藏临时骨架，避免干扰用户观察
    tmp_armature.select_set(False)
    tmp_armature.hide_set(True)
    return tmp_armature


def show_collection(collection):
    """把集合在视图层里恢复成可见：取消排除、关掉隐藏"""
    layer_collection = find_layer_collection_by_name(
        bpy.context.view_layer.layer_collection, collection.name)
    if layer_collection is None:
        return

    layer_collection.exclude = False
    layer_collection.hide_viewport = False


def repoint_armature_modifiers(mesh_objects, tmp_armature):
    """把临时网格的骨架修改器指向临时骨架"""
    for obj in mesh_objects:
        for modifier in obj.modifiers:
            if modifier.type == 'ARMATURE':
                modifier.object = tmp_armature


@timeit
def create_tmp_obj(context, objects, tmp_col):
    copied_objs = []
    for obj in objects:
        copied_obj = copy_obj(obj, tmp_col)
        # 打上临时对象标记，join 之后标记会留在合并后的对象上
        copied_obj[TMP_OBJECT_TAG] = True
        copied_objs.append(copied_obj)

    ensure_object_mode(context)
    select_only_objects(context, copied_objs)

    for copied_obj in copied_objs:
        # 应用形态键
        if copied_obj.data.shape_keys:
            apply_shape_key_mix(copied_obj)
        # 移除骨架修改器之外的修改器
        for modifier in list(copied_obj.modifiers):
            if modifier.type != 'ARMATURE':
                copied_obj.modifiers.remove(modifier)

    if len(copied_objs) > 1:
        bpy.ops.object.join()

    tmp_obj = context.view_layer.objects.active
    # join 之后标记应该已经在活动对象上了，这里再确保一次（只 join 一个对象时同理）
    tmp_obj[TMP_OBJECT_TAG] = True
    return tmp_obj


@timeit
def classify_vertices(obj, part_vgs, threshold=0.25):
    """
    按顶点组权重把顶点归类到身体部件

    规则：把一个顶点在「同一部件内的所有顶点组」上的权重加起来，
    取第一个（按 part_vgs 顺序）总和超过 threshold 的部件。
    每个顶点最多归入一个部件。
    """
    part_names = list(part_vgs.keys())
    part_count = len(part_names)

    # 顶点组名称 -> 顶点组索引（顶点组索引是从 0 开始的连续整数）
    vertex_groups = {
        vg.name: vg.index
        for vg in obj.vertex_groups
    }

    # 建查表：顶点组索引 -> 所属部件序号。
    # 用列表而不是字典，因为顶点组索引本身就是连续整数，列表查表更快。
    # 前提：part_vgs 里各部件的顶点组互不重叠，一个顶点组只属于一个部件。
    table_size = max(vertex_groups.values(), default=-1) + 1
    vg_to_part = [-1] * table_size  # -1 = 这个顶点组不属于任何关注的部件
    for part_index, vg_names in enumerate(part_vgs.values()):
        for name in vg_names:
            vg_index = vertex_groups.get(name)
            if vg_index is not None:
                vg_to_part[vg_index] = part_index

    part_vertices = OrderedDict((part, []) for part in part_names)
    # 提前把每个部件的 append 取出来，循环里少一次属性查找
    append_to_part = [part_vertices[part].append for part in part_names]

    # enumerate 自带计数，省掉每次读 vertex.index，二者顺序一致
    for vertex_index, vertex in enumerate(obj.data.vertices):
        weights = [0.0] * part_count
        for element in vertex.groups:
            part_index = vg_to_part[element.group]
            if part_index >= 0:
                weights[part_index] += element.weight

        for part_index in range(part_count):
            if weights[part_index] > threshold:
                append_to_part[part_index](vertex_index)
                break

    return part_vertices


@timeit
def create_part_meshes(props, obj, part_vertices, col, sphere_parts=(), sphere_col=None):
    """
    把临时网格按身体部件拆成多个代理网格

    1. 每个部件各建一个网格，只包含属于该部件的顶点和面
    2. 把原始网格的顶点组权重抄过去（这样骨架修改器才能正常带动代理网格）
    3. 用 Decimate 减面（用于加速后续碰撞检测）
    4. 把原始网格的 mmd_armature 修改器复制给每个代理网格
    """
    # 延迟导入，避免拖慢插件加载速度
    import numpy as np

    part_names = [part for part, indices in part_vertices.items() if indices]
    if not part_names:
        return {}

    ensure_object_mode(bpy.context)

    src_mesh = obj.data
    vertex_count = len(src_mesh.vertices)
    part_index_of = {part: i for i, part in enumerate(part_names)}

    # ------------------------------------------------------------------
    # 1. 建两张查表：旧顶点索引 -> 新顶点索引，顶点 -> 所属部件序号
    # ------------------------------------------------------------------
    # np.full(顶点数, -1)：先全部填 -1，表示「还没分配 / 不属于任何部件」
    old_to_new = np.full(vertex_count, -1, dtype=np.int32)
    vertex_to_part = np.full(vertex_count, -1, dtype=np.int32)
    part_indices = []
    for part in part_names:
        # classify_vertices 给的是 Python 列表，转成 numpy 数组才能做向量化运算
        indices = np.asarray(part_vertices[part], dtype=np.int32)
        part_indices.append(indices)
        # 新网格里顶点是连续编号 0,1,2,...，所以「旧索引 -> 新索引」就是
        # 把这些旧索引位置上的值依次填成 0,1,2,...。
        # 例：indices = [7, 3, 9]  =>  old_to_new[7]=0, old_to_new[3]=1, old_to_new[9]=2
        old_to_new[indices] = np.arange(len(indices), dtype=np.int32)
        # 同时记下这些顶点属于第几个部件
        vertex_to_part[indices] = part_index_of[part]

    # ------------------------------------------------------------------
    # 2. 一次性把坐标和拓扑读成 numpy 数组
    # ------------------------------------------------------------------
    # 后面全靠数组切片，不再逐个访问 obj.data.vertices / polygons（那个很慢）
    all_coords = np.empty(vertex_count * 3, dtype=np.float32)
    src_mesh.vertices.foreach_get("co", all_coords)
    all_coords = all_coords.reshape(vertex_count, 3)  # 摊平成 (顶点数, 3)

    # 2 ─ 7 ─ 3
    # │   │   │
    # 4 ─ 8 ─ 6
    # │   │   │
    # 0 ─ 5 ─ 1
    # 例如一个平面由四个四边面构成。                         loop为面角，例如一个顶点在不同面上可以有不同的UV位置
    # loop_verts:[8 6 3 7 4 8 7 2 0 5 8 4 5 1 6 8]     构成loop的顶点
    # loop_starts:[ 0  4  8 12]                        每个面第一个loop对应索引
    # loop_totals:[4 4 4 4]                            面的loop数量
    polygon_count = len(src_mesh.polygons)
    loop_count = len(src_mesh.loops)
    loop_verts = np.empty(loop_count, dtype=np.int32)
    src_mesh.loops.foreach_get("vertex_index", loop_verts)
    loop_starts = np.empty(polygon_count, dtype=np.int32)
    src_mesh.polygons.foreach_get("loop_start", loop_starts)
    loop_totals = np.empty(polygon_count, dtype=np.int32)
    src_mesh.polygons.foreach_get("loop_total", loop_totals)

    # ------------------------------------------------------------------
    # 3. 判断每个多边形属于哪个部件
    # ------------------------------------------------------------------
    # 规则：一个多边形的所有顶点都落在同一个部件里时，它才属于那个部件。
    #
    # 做法：先算出每个 loop 的顶点属于哪个部件，然后按多边形分组求「最小/最大部件号」。
    # 如果某个多边形的 min == max，说明它所有顶点都在同一个部件；
    # 如果 min != max（比如一个面横跨 TORSO 和 UPPER ARM），就说明这个面要被丢掉。
    #
    # loop_verts 是「每个 loop 对应哪个顶点」，vertex_to_part 是「每个顶点属于哪个部件」，
    # 那么 vertex_to_part[loop_verts] 就是「每个 loop 属于哪个部件」——
    # 这一行就叫「按索引取数」，一次把 76 万个 loop 全部算完。
    # 按照loop_verts中的索引值，从vertex_to_part中批量取值，生成一个新数组。
    part_of_loop = vertex_to_part[loop_verts]

    # 例：
    # poly_min:[0 0 0 0]
    # poly_max:[2 2 3 3]
    # poly_part:[-1 -1 -1 -1]
    # reduceat 按 loop_starts 把 part_of_loop 分段，每段求最小值/最大值。
    poly_min = np.minimum.reduceat(part_of_loop, loop_starts)
    poly_max = np.maximum.reduceat(part_of_loop, loop_starts)
    # np.where(条件, 条件成立时的值, 条件不成立时的值)
    poly_part = np.where(poly_min == poly_max, poly_min, -1)

    # 再建一张「每个 loop 属于第几个多边形」的表，用来一次性筛出某个部件的所有面。
    # np.repeat([0,1,2,...], loop_totals)：第 p 个多边形有几个 loop，就把 p 重复几次
    # loop_totals:[4 4 4 4]
    # polygon_count:4
    # poly_of_loop = np.array([
    #     0, 0, 0, 0,
    #     1, 1, 1, 1,
    #     2, 2, 2, 2,
    #     3, 3, 3, 3
    # ])
    poly_of_loop = np.repeat(np.arange(polygon_count, dtype=np.int32), loop_totals)
    # 于是 poly_part[poly_of_loop] 就是「每个 loop 所在的多边形属于哪个部件」
    # 按照poly_of_loop中的索引，从poly_part中批量取值，生成一个新数组。
    part_of_loop_by_poly = poly_part[poly_of_loop]

    # ------------------------------------------------------------------
    # 4. 逐个部件建网格
    # ------------------------------------------------------------------
    part_objs = {}
    for part_index, part in enumerate(part_names):
        indices = part_indices[part_index]

        # poly_part == part_index 是布尔数组
        sel_polys = poly_part == part_index
        # 从loop_totals中筛选出sel_polys为True的元素
        counts = loop_totals[sel_polys]

        # 面的顶点：先筛出属于本部件的所有 loop，再把「旧顶点索引」换成「新顶点索引」。
        # old_to_new[...] 就是那张查表，一次把几万个索引全部替换掉。
        new_loops = old_to_new[loop_verts[part_of_loop_by_poly == part_index]]

        # new_loops 是一长条扁平数组，要按每个面的顶点数切开。
        # cumsum 累积和，会从左到右，依次计算数组元素的累加结果。
        # counts = np.array([3, 4, 4])
        # result = np.cumsum(counts)
        # [3, 7, 11]
        # np.cumsum(counts)[:-1] 算出每个面的结束位置，np.split 在这些位置切开，-1表示结束的位置，所以不需要切开
        # 例：
        # counts = np.array([3, 4])
        # new_loops = np.array([0, 1, 2, 3, 4, 5, 6])
        # np.cumsum(counts) # [3, 7]
        # np.cumsum(counts)[:-1] # [3]
        # faces = np.split(new_loops, [3]) #[array([0, 1, 2]), array([3, 4, 5, 6])]
        # 注意：断点数组为空时 np.split 会把整个数组当成一块返回（变成一个超大面），
        # 所以必须判空。实测：np.split([1,2,3], []) -> [[1,2,3]]
        if counts.size:
            faces = np.split(new_loops, np.cumsum(counts)[:-1])
        else:
            faces = []

        mesh = bpy.data.meshes.new(f"{part}")
        # https://docs.blender.org/api/current/bpy.types.Mesh.html#bpy.types.Mesh.from_pydata
        # tolist() 转成 Python 列表
        mesh.from_pydata(all_coords[indices].tolist(), [], faces)
        mesh.update()

        part_obj = bpy.data.objects.new(f"{part}", mesh)
        # 打上临时对象标记
        part_obj[TMP_OBJECT_TAG] = True
        # 罩体专用的部件建在「罩体」集合里：检测结束后要留着，不能混进临时集合
        if sphere_col is not None and part in sphere_parts:
            sphere_col.objects.link(part_obj)
        else:
            col.objects.link(part_obj)
        part_obj.matrix_world = obj.matrix_world.copy()

        # 复制顶点组。
        for vertex_group in obj.vertex_groups:
            part_obj.vertex_groups.new(name=vertex_group.name)

        # 复制顶点权重。
        # 关键：把每个顶点组的 add 方法先取成列表。直接写
        # part_obj.vertex_groups[i].add(...) 每次都要做一次集合索引查找，
        # 实测慢 3.3 倍（TORSO 单部件 0.349s -> 0.105s）。
        adders = [vertex_group.add for vertex_group in part_obj.vertex_groups]
        for new_index, old_index in enumerate(indices):
            for group in src_mesh.vertices[old_index].groups:
                adders[group.group]([new_index], group.weight, 'REPLACE')

        part_objs[part] = part_obj

    # ------------------------------------------------------------------
    # 5. 减面
    # ------------------------------------------------------------------
    proxy_accuracy = props.proxy_accuracy
    for part, part_obj in part_objs.items():
        # 罩体专用的手部代理网格不减面
        if part in sphere_parts:
            continue

        face_count = len(part_obj.data.polygons)

        select_only_objects(bpy.context, [part_obj])
        m_name = "Decimate"
        modifier = part_obj.modifiers.new(name=m_name, type='DECIMATE')
        modifier.decimate_type = 'COLLAPSE'

        if proxy_accuracy == "FULL":
            modifier.ratio = 1
        elif proxy_accuracy == "BALANCED":
            modifier.ratio = min(2000 / face_count, 1)
        else:
            modifier.ratio = min(500 / face_count, 1)

        modifier.use_symmetry = True
        bpy.ops.object.modifier_apply(modifier=m_name)

    # ------------------------------------------------------------------
    # 6. 从「活动对象」复制修改器到「所有选中对象」，然后删掉临时网格
    # ------------------------------------------------------------------
    select_only_objects(bpy.context, list(part_objs.values()) + [obj])
    bpy.ops.object.modifier_copy_to_selected(modifier="mmd_armature")

    bpy.data.objects.remove(obj, do_unlink=True)

    # 7. 平滑着色（用自动平滑着色，每帧多算一遍法线，耗时约 +20%）
    for part_obj in part_objs.values():
        polygons = part_obj.data.polygons
        polygons.foreach_set("use_smooth", [True] * len(polygons))
        part_obj.data.update()

    # 8. 罩体专用的手部代理网格隐藏（眼睛图标）：它只在罩体 Shrinkwrap 时当目标用。
    #    必须放在最后——上面复制骨架修改器要靠选中，隐藏的对象选不中
    for part in sphere_parts:
        part_obj = part_objs.get(part)
        if part_obj is not None:
            part_obj.hide_set(True)

    return part_objs


@timeit
def remove_part_meshes(tmp_col, tmp_objs=None, remove_empty_collection=False):
    """删除穿模检测用的临时网格对象"""
    targets = []
    seen = set()

    for part_obj in tmp_objs or ():
        if part_obj is None:
            continue
        try:
            pointer = part_obj.as_pointer()
        except ReferenceError:
            # 已经被删掉了（重复清理时会出现），跳过
            continue
        if pointer not in seen:
            seen.add(pointer)
            targets.append(part_obj)

    # 兜底：临时集合里任何带标记的对象都算临时对象
    for obj in tmp_col.objects:
        if TMP_OBJECT_TAG not in obj.keys():
            continue
        pointer = obj.as_pointer()
        if pointer not in seen:
            seen.add(pointer)
            targets.append(obj)

    if targets:
        # 连带记下网格 / 骨架数据块：删对象不会删数据，users 归零时要手动清掉
        doomed = [
            (obj, obj.data if obj.type in {'MESH', 'ARMATURE'} else None)
            for obj in targets
        ]

        # 罩体的 Shrinkwrap.offset 关键帧有一个 Action，对象删掉后 Action 不会跟着消失，
        # 这里一起记下来，数据块删干净后再清掉已经没人用的 Action
        doomed_actions = set()
        for obj, data in doomed:
            animation_data = getattr(obj, "animation_data", None)
            if animation_data is not None and animation_data.action is not None:
                doomed_actions.add(animation_data.action)
            shape_keys = getattr(data, "shape_keys", None) if data is not None else None
            if shape_keys is not None:
                key_animation = getattr(shape_keys, "animation_data", None)
                if key_animation is not None and key_animation.action is not None:
                    doomed_actions.add(key_animation.action)

        for obj, _data in doomed:
            bpy.data.objects.remove(obj, do_unlink=True)

        for _obj, data in doomed:
            if data is None or data.users != 0:
                continue
            if data.id_type == 'MESH':
                bpy.data.meshes.remove(data)
            elif data.id_type == 'ARMATURE':
                bpy.data.armatures.remove(data)

        for action in doomed_actions:
            try:
                if action.users == 0:
                    bpy.data.actions.remove(action)
            except (ReferenceError, RuntimeError):
                continue

    if not remove_empty_collection:
        return

    is_empty = not tmp_col.objects and not tmp_col.children
    if is_empty:
        bpy.data.collections.remove(tmp_col)


@timeit
def purge_temp_datablocks(mesh_pointers_before=None, key_pointers_before=None):
    """
    清掉检测过程中新产生、但已经没人在用的网格和形态键数据块

    只处理「执行前不存在」的数据块，用户文件里原本就有的孤立数据不会被动到。
    """
    # 孤立网格：copy_obj 的副本、join 之后遗留的源网格
    if mesh_pointers_before is not None:
        leftover_meshes = [
            mesh for mesh in bpy.data.meshes
            if mesh.as_pointer() not in mesh_pointers_before and mesh.users == 0
        ]
        for mesh in leftover_meshes:
            bpy.data.meshes.remove(mesh)

    if key_pointers_before is None:
        return

    # 形态键（Key）不能用 users == 0 判断：Blender 给 Key 这类数据块固定算了
    # 一个「自带用户」，所以 shape_key_clear() 之后它的 users 仍然显示 1，
    # 只能反过来看还有没有网格的 shape_keys 指向它。
    used_keys = {
        mesh.shape_keys.as_pointer()
        for mesh in bpy.data.meshes
        if mesh.shape_keys is not None
    }
    leftover_keys = [
        key for key in bpy.data.shape_keys
        if key.as_pointer() not in key_pointers_before
           and key.as_pointer() not in used_keys
    ]
    if leftover_keys:
        # bpy.data.shape_keys 没有 remove() 方法（Key 属于特殊类型的数据块），
        # 只能走 batch_remove
        # https://docs.blender.org/api/current/bpy.types.BlendData.html#bpy.types.BlendData.batch_remove
        bpy.data.batch_remove(ids=leftover_keys)


def get_bvh_for(obj, depsgraph):
    obj_eval = obj.evaluated_get(depsgraph)
    return mathutils.bvhtree.BVHTree.FromObject(obj_eval, depsgraph)


def iter_detect_collisions(pairs, start, end, merge_on_type_change=True, pair_names=None,
                           hand_spheres=None):
    """
    逐帧检测各部件代理网格之间是否相交（生成器版本）

    每处理完一帧就 yield 一次 (已处理帧数, 总帧数)，让调用方有机会把控制权还给
    Blender 刷新界面；检测结束时的返回值是 (merged, frame_hits)，
    从 StopIteration.value 里取。同步版本见 detect_collisions_between_objects。

    hand_spheres 不为空时，每帧先调整罩体的 Shrinkwrap.offset（步长 0.001）并打关键帧，
    有罩体的一侧直接用罩体作为手部参与碰撞；没有罩体时才退回手部代理网格。

    实测（250 帧，250 个无关网格全程保留）：
        scene.frame_set(f)                          1.010 s
        scene.frame_current = f + view_layer.update()  0.349 s   ← 快 2.9 倍
        把那 250 个网格全删掉后再 frame_set          0.318 s   ← 理论上限
    """
    scene = bpy.context.scene

    # 逐帧结果：frame -> [穿模类型, ...]（同一帧里命中的多个部件对都会收进来）
    frame_hits = {}
    total = end - start + 1

    hand_spheres = list(hand_spheres or ())
    # 手部罩体按「它所属的手部代理网格」归类：检测对里出现这个网格时，就把它这一侧的罩体也测一遍
    hand_sphere_map = {}
    for sphere in hand_spheres:
        hand_sphere_map.setdefault(sphere.hand_obj.name, []).append(sphere)

    for index, frame in enumerate(range(start, end + 1), start=1):
        # 直接修改scene.frame_current确实不会触发完整的数据更新，但frame_set()的开销太大，而在当前场景下，两者获取的数据一致。
        scene.frame_current = frame
        deps = bpy.context.evaluated_depsgraph_get()

        # 先更新罩体：两只手共用依赖图更新，每帧调整 Shrinkwrap.offset 并打关键帧
        sphere_trees = {}
        if hand_spheres:
            sphere_trees = update_hand_spheres(hand_spheres, frame, deps)
            # 部件几何在当前帧没有变，继续用切帧后的依赖图即可，不必再整体求值一次

        # 罩体已经包住手，碰撞时直接把罩体当成手；只保留更新成功的罩体
        active_hand_spheres = {}
        for hand_name, spheres in hand_sphere_map.items():
            active = [sphere for sphere in spheres if sphere.obj.name in sphere_trees]
            if active:
                active_hand_spheres[hand_name] = active

        # 按对象名缓存
        bvh_cache = {}

        def get_tree(obj):
            tree = bvh_cache.get(obj.name)
            if tree is None:
                tree = get_bvh_for(obj, deps)
                bvh_cache[obj.name] = tree
            return tree

        for obj_a, obj_b in pairs:
            if obj_a is None or obj_b is None:
                continue

            spheres_a = active_hand_spheres.get(obj_a.name)
            spheres_b = active_hand_spheres.get(obj_b.name)

            if spheres_a or spheres_b:
                # 罩体已经完整包住手，罩体就是真正的手部；不再建手部代理网格的 BVH
                if spheres_a and spheres_b:
                    hit = any(
                        sphere_trees[sphere_a.obj.name].overlap(sphere_trees[sphere_b.obj.name])
                        for sphere_a in spheres_a
                        for sphere_b in spheres_b
                    )
                elif spheres_a:
                    other_tree = get_tree(obj_b)
                    hit = any(
                        sphere_trees[sphere.obj.name].overlap(other_tree)
                        for sphere in spheres_a
                    )
                else:
                    other_tree = get_tree(obj_a)
                    hit = any(
                        sphere_trees[sphere.obj.name].overlap(other_tree)
                        for sphere in spheres_b
                    )
            else:
                # 没有罩体（未勾选或创建失败）时退回原来的手部代理网格检测
                tree_a = get_tree(obj_a)
                tree_b = get_tree(obj_b)
                hit = bool(tree_a.overlap(tree_b))

            if hit:
                name = pair_names.get((obj_a, obj_b)) if pair_names else None
                names = frame_hits.setdefault(frame, [])
                # 左右手可能在同一帧同时命中同一个逻辑部件对，只记一次
                if name not in names:
                    names.append(name)

        yield index, total

    merged = merge_frame_hits(frame_hits, merge_on_type_change)
    return merged, frame_hits


def merge_frame_hits(frame_hits, merge_on_type_change=True):
    """把逐帧结果 {帧: [名称]} 压成连续区间 {(起始帧, 结束帧): [名称]}"""
    merged = {}
    seg_start = None
    seg_end = None
    seg_names = None

    for frame in sorted(frame_hits):
        names = frame_hits[frame]

        # 能接上的条件：帧号连续，且（允许换类型 或 类型和当前区间完全一致）
        if seg_start is not None and frame == seg_end + 1 and (merge_on_type_change or seg_names == names):
            seg_end = frame
            # 换类型时把新类型也收进这个区间的列表
            for name in names:
                if name not in seg_names:
                    seg_names.append(name)
            continue

        # 接不上，先把上一个区间收尾
        if seg_start is not None:
            merged[(seg_start, seg_end)] = seg_names

        seg_start = frame
        seg_end = frame
        # 必须复制一份，seg_names 会做 append
        seg_names = list(names)

    if seg_start is not None:
        merged[(seg_start, seg_end)] = seg_names

    return merged


@timeit
def detect_collisions_between_objects(pairs, start, end, merge_on_type_change=True, pair_names=None,
                                      hand_spheres=None):
    """同步跑完整个检测，返回 (merged, frame_hits)；分片执行请用 iter_detect_collisions"""
    generator = iter_detect_collisions(
        pairs, start, end, merge_on_type_change, pair_names, hand_spheres)
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def get_breast_bones(armature):
    """获取胸部骨骼列表
    """
    breast_bones = []
    for b in armature.data.bones:
        b_name = b.name
        if is_dummy_bone(b_name):
            continue
        match = BREAST_BONE_PATTERN.match(b_name)
        if match:
            breast_bones.append(b)
            continue

    # 需要停止查找子级时命中的骨骼名称
    STOP_NAMES = {"首", "肩P.L", "肩.L", "肩P.R", "肩.R"}

    # 找到“上半身2”“上半身3”的骨骼
    upper_bones = [
        b for b in armature.data.bones
        if b.name in ("上半身2", "上半身3")
    ]

    # 记录已经收集的骨骼，避免重复
    collected = set(breast_bones)

    def collect_descendants(parent_bone, upper_bones):
        """递归收集符合条件的子孙节点"""
        for child in parent_bone.children:
            c_name = child.name
            if is_dummy_bone(c_name):
                continue
            # 遇到停止名称则不再深入
            if c_name in STOP_NAMES:
                continue
            # 子级骨骼的 tail 必须在所有上半身骨骼 head/tail 的 y 更小（更靠前）才会继续
            child_tail_y = child.tail_local.y
            if not all(
                    child_tail_y < ub.head_local.y and child_tail_y < ub.tail_local.y
                    for ub in upper_bones
            ):
                continue

            if child not in collected:
                breast_bones.append(child)
                collected.add(child)

            # 继续寻找子孙节点
            collect_descendants(child, upper_bones)

    for ub in upper_bones:
        collect_descendants(ub, upper_bones)

    return breast_bones

import copy
from collections import OrderedDict

import mathutils

from ..properties.clipping_detection_properties import get_ordered_detection_range
from ..utils import *

# 手（手首+手指） - 躯干（上半身+下半身+上半身2+胸部）
# 小臂（ひじ+手捩） - 躯干（上半身+下半身+上半身2+胸部）
# 大臂（腕+腕捩） - 躯干（上半身+下半身+上半身2+胸部）
# 手（手首+手指） - 腿（左右腿）
# 小臂（ひじ+手捩） - 腿（左右腿）
# 大臂（腕+腕捩） - 腿（左右腿）
# 手（手首+手指） - 头（首+头）
# 小臂（ひじ+手捩） -头（首+头）
# 大臂（腕+腕捩） - 头（首+头）
# 手（手首+手指） - 大臂（腕+腕捩）


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

TMP_ANALYSIS_COLLECTION_NAME = "穿模检测临时集合"

# 临时对象的标记属性
TMP_OBJECT_TAG = "mmd_motion_tools_tmp"


class ClippingDetectionOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.detect_clipping"
    bl_label = "执行"
    bl_description = "检测网格对象之间的穿模情况"
    bl_options = {'REGISTER', 'UNDO'}

    def check_props(self, context):
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

        objects = find_pmx_objects(armature)
        if not objects:
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

        armature, objects = result
        if not self.detect_clipping(context, armature, objects):
            return {'CANCELLED'}

        return {'FINISHED'}

    def detect_clipping(self, context, armature, objects):
        start_time = time.perf_counter()
        props = context.scene.mmd_motion_tools_detect_clipping

        tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
        # 先按对象上的标记清一次
        remove_part_meshes(tmp_col)
        # 记录执行前已有的网格、形态键数据块：后面清理临时对象时，只清理本次新建的，
        # 不会误删用户自己留在文件里的孤立数据
        mesh_pointers_before = {mesh.as_pointer() for mesh in bpy.data.meshes}
        key_pointers_before = {key.as_pointer() for key in bpy.data.shape_keys}
        tmp_obj = create_tmp_obj(context, objects, tmp_col)

        breast_bones = get_breast_bones(armature)
        bb_names = [bb.name for bb in breast_bones]
        part_vgs = copy.deepcopy(PART_VGS)
        part_vgs["BREAST"] = bb_names
        # 自定义拓展：把额外指定的骨骼（顶点组）当作独立部件 CUSTOM 参与检测，
        custom_vgs = split_names(props.custom_bones_str)
        if custom_vgs:
            part_vgs["CUSTOM"] = custom_vgs
        part_vertices = classify_vertices(tmp_obj, part_vgs)
        part_objs = create_part_meshes(props, tmp_obj, part_vertices, tmp_col)

        # 在场景切换前获取首尾帧，否则切换后将变为默认值
        frame_start = context.scene.frame_start
        frame_end = context.scene.frame_end
        # 保存原场景
        original_scene = context.scene
        # 新建空白场景，避免 Dependency graph 计算
        bpy.ops.scene.new(type='NEW')
        tmp_scene = bpy.context.scene
        # 恢复集合及内部网格的显示
        get_collection(TMP_ANALYSIS_COLLECTION_NAME)

        detection_range = get_ordered_detection_range(props)
        pair_key_map = {}
        for r in detection_range:
            a, b = r.split("-")
            pair_key_map[(part_objs.get(a), part_objs.get(b))] = r

        pairs = list(pair_key_map.keys())

        result = detect_collisions_between_objects(pairs, frame_start, frame_end, pair_names=pair_key_map)

        # 注意要用 original_scene：上面 scene.new 之后当前场景已经变了
        props = original_scene.mmd_motion_tools_detect_clipping
        props.clippings.clear()
        total_frames = 0
        for (seg_start, seg_end), names in result.items():
            item = props.clippings.add()
            item.start = seg_start
            item.end = seg_end
            item.types = ", ".join(names)
            total_frames += seg_end - seg_start + 1

        if not result:
            self.report({'INFO'}, '未检测到穿模!')
        else:
            self.report({'INFO'},
                        f'检测到 {len(result)} 段穿模，共 {total_frames} 帧')

        # 恢复原场景并删除临时场景
        bpy.context.window.scene = original_scene
        bpy.data.scenes.remove(tmp_scene)

        # 临时部件代理网格按开关删除；中间产生的孤立数据块每次都要清，
        # 因为代理网格里已经不含形态键，留着也不会被用到
        if props.remove_tmp:
            remove_part_meshes(tmp_col, part_objs.values(), True)
        purge_temp_datablocks(mesh_pointers_before, key_pointers_before)

        print(f"总耗时: {time.perf_counter() - start_time:.4f} 秒")
        return True


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
        props.clippings.clear()
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
def create_part_meshes(props, obj, part_vertices, col):
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
    for part_obj in part_objs.values():
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
    return part_objs


@timeit
def remove_part_meshes(tmp_col, tmp_objs=None, remove_empty_collection=False):
    """删除穿模检测用的临时网格对象"""
    targets = []
    seen = set()

    for part_obj in tmp_objs or ():
        if part_obj is None:
            continue
        pointer = part_obj.as_pointer()
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
        doomed = [(obj, obj.data if obj.type == 'MESH' else None) for obj in targets]

        for obj, _mesh in doomed:
            bpy.data.objects.remove(obj, do_unlink=True)

        for _obj, mesh in doomed:
            # 删除对象不会连带删除网格数据，users 归零时手动清掉，避免残留孤儿数据
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)

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


@timeit
def detect_collisions_between_objects(pairs, start, end, merge_on_type_change=True, pair_names=None):
    """
    逐帧检测各部件代理网格之间是否相交

    实测（250 帧，250 个无关网格全程保留）：
        scene.frame_set(f)                          1.010 s
        scene.frame_current = f + view_layer.update()  0.349 s   ← 快 2.9 倍
        把那 250 个网格全删掉后再 frame_set          0.318 s   ← 理论上限
    """
    scene = bpy.context.scene

    # 逐帧结果：frame -> 穿模类型（同一帧只记第一个出现的穿模类型）
    frame_hits = {}

    for frame in range(start, end + 1):
        # 直接修改scene.frame_current确实不会触发完整的数据更新，但frame_set()的开销太大，而在当前场景下，两者获取的数据一致。
        scene.frame_current = frame
        deps = bpy.context.evaluated_depsgraph_get()

        # 按对象名缓存
        bvh_cache = {}

        for obj_a, obj_b in pairs:
            if obj_a is None or obj_b is None:
                continue

            tree_a = bvh_cache.get(obj_a.name)
            if tree_a is None:
                tree_a = get_bvh_for(obj_a, deps)
                bvh_cache[obj_a.name] = tree_a

            tree_b = bvh_cache.get(obj_b.name)
            if tree_b is None:
                tree_b = get_bvh_for(obj_b, deps)
                bvh_cache[obj_b.name] = tree_b

            if tree_a.overlap(tree_b):
                name = pair_names.get((obj_a, obj_b)) if pair_names else None
                frame_hits[frame] = name
                break

    merged = {}
    seg_start = None
    seg_end = None
    seg_names = None

    # 合并连续帧：把 {frame: 名称} 压成 {(start, end): [名称]}
    for frame in sorted(frame_hits):
        name = frame_hits[frame]

        # 能接上的条件：帧号连续，且（允许换类型 或 类型和当前区间一致）
        if seg_start is not None and frame == seg_end + 1 and (merge_on_type_change or seg_names == [name]):
            seg_end = frame
            # 换类型时把新类型也收进这个区间的列表
            if name not in seg_names:
                seg_names.append(name)
            continue

        # 接不上，先把上一个区间收尾
        if seg_start is not None:
            merged[(seg_start, seg_end)] = seg_names

        seg_start = frame
        seg_end = frame
        seg_names = [name]

    if seg_start is not None:
        merged[(seg_start, seg_end)] = seg_names

    return merged


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

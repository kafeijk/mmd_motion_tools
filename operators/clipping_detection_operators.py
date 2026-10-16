from collections import OrderedDict

import mathutils

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


class ClippingDetectionOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.detect_clipping"
    bl_label = "执行"
    bl_description = "检测网格对象之间的穿模情况"
    bl_options = {'REGISTER', 'UNDO'}

    def check_props(self, context):
        return True

    def execute(self, context):
        result = self.check_props(context)
        if result is None:
            return {'CANCELLED'}

        if not self.detect_clipping(context):
            return {'CANCELLED'}

        return {'FINISHED'}

    def detect_clipping(self, context):
        start_time = time.perf_counter()

        root = find_pmx_root()
        armature = find_pmx_armature(root)
        objects = find_pmx_objects(armature)

        tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)
        tmp_obj = create_tmp_obj(context, objects, tmp_col)

        part_vertices = classify_vertices(tmp_obj)

        part_objs = create_part_meshes(tmp_obj, part_vertices, tmp_col)

        bpy.ops.scene.new(type='NEW')

        pairs = [
            (part_objs.get("HAND"), part_objs.get("TORSO")),
            (part_objs.get("FOREARM"), part_objs.get("TORSO")),
            (part_objs.get("UPPER_ARM"), part_objs.get("TORSO")),

            (part_objs.get("HAND"), part_objs.get("LOWER_LIMBS")),
            (part_objs.get("FOREARM"), part_objs.get("LOWER_LIMBS")),
            (part_objs.get("UPPER_ARM"), part_objs.get("LOWER_LIMBS")),

            (part_objs.get("HAND"), part_objs.get("HEAD")),
            (part_objs.get("FOREARM"), part_objs.get("HEAD")),
            (part_objs.get("UPPER_ARM"), part_objs.get("HEAD")),

            (part_objs.get("HAND"), part_objs.get("UPPER_ARM")),

        ]
        tmp_col = get_collection(TMP_ANALYSIS_COLLECTION_NAME)

        # to_delete = []
        # for obj in bpy.data.objects:
        #     if obj.type == "MESH" and obj.name not in tmp_col.objects:
        #         to_delete.append(obj)
        # for d in to_delete:
        #     bpy.data.objects.remove(d, do_unlink=True)
        detect_collisions_between_objects(pairs, 1, 250)
        print(f"总耗时: {time.perf_counter() - start_time:.4f} 秒")
        return True


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
    return tmp_obj


@timeit
def classify_vertices(obj, threshold=0.25):
    """
    按顶点组权重把顶点归类到身体部件

    规则：把一个顶点在「同一部件内的所有顶点组」上的权重加起来，
    取第一个（按 PART_VGS 顺序）总和超过 threshold 的部件。
    每个顶点最多归入一个部件。
    """
    part_names = list(PART_VGS.keys())
    part_count = len(part_names)

    # 顶点组名称 -> 顶点组索引（顶点组索引是从 0 开始的连续整数）
    vertex_groups = {
        vg.name: vg.index
        for vg in obj.vertex_groups
    }

    # 建查表：顶点组索引 -> 所属部件序号。
    # 用列表而不是字典，因为顶点组索引本身就是连续整数，列表查表更快。
    # 前提：PART_VGS 里各部件的顶点组互不重叠，一个顶点组只属于一个部件。
    table_size = max(vertex_groups.values(), default=-1) + 1
    vg_to_part = [-1] * table_size  # -1 = 这个顶点组不属于任何关注的部件
    for part_index, vg_names in enumerate(PART_VGS.values()):
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
def create_part_meshes(obj, part_vertices, col):
    part_objs = {}
    for part, vertex_indices in part_vertices.items():
        if not vertex_indices:
            continue

        # 旧顶点索引 -> 新顶点索引
        index_map = {
            old_index: new_index
            for new_index, old_index in enumerate(vertex_indices)
        }

        # 新网格坐标
        vert_cos = [
            obj.data.vertices[index].co
            for index in vertex_indices
        ]

        faces = []
        for polygon in obj.data.polygons:
            # 仅保留所有顶点都属于当前部件的面
            if not all(index in index_map for index in polygon.vertices):
                continue
            # 将原网格的顶点索引转换为新网格的顶点索引
            face = [
                index_map[index]
                for index in polygon.vertices
            ]
            faces.append(face)

        mesh = bpy.data.meshes.new(f"{part}")
        # https://docs.blender.org/api/current/bpy.types.Mesh.html#bpy.types.Mesh.from_pydata
        mesh.from_pydata(vert_cos, [], faces)
        mesh.update()

        part_obj = bpy.data.objects.new(f"{part}", mesh)
        col.objects.link(part_obj)
        part_obj.matrix_world = obj.matrix_world.copy()

        # 复制顶点组
        for vertex_group in obj.vertex_groups:
            part_obj.vertex_groups.new(name=vertex_group.name)

        # 复制顶点权重
        for old_index, new_index in index_map.items():
            vertex = obj.data.vertices[old_index]
            for group in vertex.groups:
                part_obj.vertex_groups[group.group].add(
                    [new_index],
                    group.weight,
                    'REPLACE',
                )

        part_objs[part] = part_obj

    for part_obj in part_objs.values():
        deselect_all_objects()
        select_and_activate(part_obj)
        m_name = "Decimate"
        modifier = part_obj.modifiers.new(name=m_name, type='DECIMATE')
        modifier.decimate_type = 'COLLAPSE'
        modifier.ratio = 0.1
        bpy.ops.object.modifier_apply(modifier=m_name)

    deselect_all_objects()
    for part_obj in part_objs.values():
        select_and_activate(part_obj)
    select_and_activate(obj)
    bpy.ops.object.modifier_copy_to_selected(modifier="mmd_armature")

    bpy.data.objects.remove(obj, do_unlink=True)
    return part_objs


def get_bvh_for(obj, depsgraph):
    obj_eval = obj.evaluated_get(depsgraph)
    return mathutils.bvhtree.BVHTree.FromObject(obj_eval, depsgraph)


@timeit
def detect_collisions_between_objects(pairs, start, end):
    scene = bpy.context.scene
    results = {pair: [] for pair in pairs}

    for frame in range(start, end + 1):
        scene.frame_set(frame)
        deps = bpy.context.evaluated_depsgraph_get()

        for obj_a, obj_b in pairs:
            if obj_a is None or obj_b is None:
                continue

            tree_a = get_bvh_for(obj_a, deps)
            tree_b = get_bvh_for(obj_b, deps)

            if tree_a.overlap(tree_b):
                results[(obj_a, obj_b)].append((frame, "Collision"))
                break

    for (obj_a, obj_b), events in results.items():
        if events:
            print(f"\n{obj_a.name} ↔ {obj_b.name}")
            for frame, typ in events:
                print(f"  Frame {frame}: {typ}")
        else:
            print(f"\n{obj_a.name} ↔ {obj_b.name}: No collisions or proximities detected.")

    return results

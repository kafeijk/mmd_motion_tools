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


@timeit
def create_tmp_obj(context, objects, tmp_col):
    copied_objs = []
    for obj in objects:
        copied_obj = copy_obj(obj, tmp_col)
        copied_objs.append(copied_obj)

    for copied_obj in copied_objs:
        deselect_all_objects()
        select_and_activate(copied_obj)
        # 应用形态键
        if copied_obj.data.shape_keys:
            bpy.ops.object.shape_key_remove(all=True, apply_mix=True)
        # 移除骨架修改器之外的修改器
        for modifier in list(copied_obj.modifiers):
            if modifier.type != 'ARMATURE':
                copied_obj.modifiers.remove(modifier)

    if len(copied_objs) > 1:
        deselect_all_objects()
        for copied_obj in copied_objs:
            select_and_activate(copied_obj)
        bpy.ops.object.join()
    else:
        select_and_activate(copied_objs[0])

    tmp_obj = context.view_layer.objects.active
    return tmp_obj


@timeit
def classify_vertices(obj, threshold=0.25):
    part_vertices = OrderedDict((part, []) for part in PART_VGS)

    vertex_groups = {
        vg.name: vg.index
        for vg in obj.vertex_groups
    }

    part_vg_indexes = OrderedDict()
    for part, vg_names in PART_VGS.items():
        vg_indexes = {
            vertex_groups[name]
            for name in vg_names
            if name in vertex_groups
        }
        part_vg_indexes[part] = vg_indexes

    for vertex in obj.data.vertices:
        for part, vg_indexes in part_vg_indexes.items():
            weight = sum(
                group.weight
                for group in vertex.groups
                if group.group in vg_indexes
            )

            if weight > threshold:
                part_vertices[part].append(vertex.index)
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

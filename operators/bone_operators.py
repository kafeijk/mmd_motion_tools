from ..utils import *


class SimplifyBoneOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.simplify_bone"
    bl_label = "精简骨骼"
    bl_description = "隐藏不参与K帧的骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    display_type: bpy.props.EnumProperty(
        name="显示为",
        description="显示骨骼的样式",
        items=[
            ("OCTAHEDRAL", "八面锥", "八面锥"),
            ("STICK", "棍形", "棍形"),
        ],
        default="STICK",
    )

    bake_mode: bpy.props.EnumProperty(
        name="显示范围",
        description="参与K帧的骨骼",
        items=[
            ("ARM1", "手臂", "手臂骨骼（不含捩骨）"),
            ("ARM2", "手臂（含捩骨）", "手臂骨骼（包含捩骨）"),
            ("KEYFRAME", "K帧骨骼", "用于K帧的常用骨骼"),
            ("DEFAULT", "默认骨骼", "点击按钮前的骨骼显示情况"),
        ],
        default="ARM1",
    )

    def execute(self, context):
        armatures = self.check_props(context)
        if not armatures:
            return {'CANCELLED'}

        for armature in armatures:
            if ".dummy_armature" in armature.name:
                continue

            show_object(armature)
            select_bake_bone(armature, self.bake_mode, self.display_type)
        return {'FINISHED'}

    def check_props(self, context):
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "未启用mmd_tools插件！")
            return None

        obj = context.active_object

        if not obj:
            self.report({'ERROR'}, "请选择模型!")
            return None

        root = find_ancestor(obj)
        armatures = find_children(root, "ARMATURE")
        if not armatures:
            self.report({'ERROR'}, "未找到模型骨架")
            return None

        return armatures


def select_bake_bone(armature, mode, display_type):
    """选择用于烘焙动作的骨骼"""
    bone_modes = {
        "ARM1": PMX_BAKE_BONES_ARM1,
        "ARM2": PMX_BAKE_BONES_ARM2,
        "KEYFRAME": PMX_BAKE_BONES,
    }

    # 首次点击时记录骨骼原始显示/隐藏状态，已有记录则不覆盖
    save_original_bone_hide(armature)

    if mode in bone_modes:
        bake_bones = set(bone_modes[mode])

        for pb in armature.pose.bones:
            is_bake_bone = pb.mmd_bone.name_j in bake_bones
            # 保留预设骨骼，隐藏其他骨骼
            set_bone_hide(pb, not is_bake_bone)
    else:
        # 默认骨骼：恢复为原始显示/隐藏状态
        restore_original_bone_hide(armature)

    armature.data.display_type = display_type

    # 标记更新，避免多次、频繁点击下骨骼显示不准确
    armature.update_tag()


class FlipPoseOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.flip_pose"
    bl_label = "翻转姿态"
    bl_description = "翻转姿态"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature = self.check_props(context)
        if not armature:
            return {'CANCELLED'}
        self.flip_bone(armature)
        return {'FINISHED'}

    def flip_bone(self, armature):
        # 未启用 MMD Tools 则当做普通模型
        if not is_mmd_tools_enabled():
            bpy.ops.pose.copy()
            bpy.ops.pose.paste(flipped=True)
            return

        root = find_pmx_root_with_child(armature)
        if root:
            bpy.ops.mmd_tools.flip_pose()
        else:
            bpy.ops.pose.copy()
            bpy.ops.pose.paste(flipped=True)

    def get_armature(self, context):
        obj = context.active_object

        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, '请选择骨架对象!')
            return None

        if context.mode != 'POSE':
            self.report({'ERROR'}, '请进入姿态模式!')
            return None

        bones = get_selected_bones(obj)
        if not bones:
            self.report({'ERROR'}, '请选择骨骼!')
            return None

        return obj

    def check_props(self, context):
        armature = self.get_armature(context)
        if not armature:
            return None

        return armature


def get_visible_bone_names(armature):
    """
    可见骨骼名集合

    骨骼被隐藏、或者所属的骨骼集合都不可见，都当作「不存在」，关联查找不会穿过它们。
    不属于任何集合的骨骼算可见（刚建好的骨架、脚本新建的骨骼经常是这种情况）。
    """
    if bpy.app.version < (4, 0, 0):
        layers = armature.data.layers
        return {
            bone.name for bone in armature.data.bones
            if not bone.hide and any(layers[i] for i, enabled in enumerate(bone.layers) if enabled)
        }

    def is_visible(collection):
        # is_visible_effectively 会把父级集合的隐藏也考虑进来（4.2+ 才有）
        visible = getattr(collection, "is_visible_effectively", None)
        return collection.is_visible if visible is None else visible

    names = set()
    for bone in armature.data.bones:
        if bone.hide:
            continue
        collections = list(bone.collections)
        if not collections or any(is_visible(collection) for collection in collections):
            names.add(bone.name)
    return names


def traverse_linked_parents(visible_names, bone):
    """
    父链：从当前骨骼往上找，只在「父级只有一个子骨骼」时继续

    和 Blender 原生的「选择关联项」不同：原生会穿过分叉继续往上，
    这里遇到分叉（父级有多个子骨骼）就停下，避免选到不该选的整根脊柱。
    """
    names = []
    parent = bone.parent
    while parent is not None and parent.name in visible_names and len(parent.children) == 1:
        names.append(parent.name)
        parent = parent.parent
    return names


def traverse_linked_children(visible_names, bone):
    """子链：从当前骨骼往下找，只在「独生子」关系上继续，遇到分叉或隐藏骨骼就停"""
    names = []
    current = bone
    while len(current.children) == 1:
        child = current.children[0]
        if child.name not in visible_names:
            break
        names.append(child.name)
        current = child
    return names


class SelectLinkedBoneOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.select_linked_bone"
    bl_label = "关联骨骼"
    bl_description = "在选中骨骼的基础上，沿父/子关系选择整条骨链" \
                     "（遇到分叉或隐藏骨骼就停止；需在姿态模式或编辑模式下使用）"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature = context.active_object

        # 只需为骨架即可，不限于MMD模型
        if armature is None or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "请选择骨架！")
            return {'CANCELLED'}

        if armature.mode not in {'POSE', 'EDIT'}:
            self.report({'ERROR'}, "请进入姿态模式！")
            return {'CANCELLED'}

        # 按骨架当前模式取选中骨骼，避免选择状态未同步
        if armature.mode == 'EDIT':
            selected = [bone.name for bone in armature.data.edit_bones if bone.select]
        else:
            selected = [pb.name for pb in armature.pose.bones if is_pose_bone_selected(pb)]

        if not selected:
            self.report({'ERROR'}, "请选择骨骼！")
            return {'CANCELLED'}

        # 遍历用的是 bones（静止数据），它在任何模式下都能查到父子关系
        bones = armature.data.bones
        visible_names = get_visible_bone_names(armature)
        linked = set()
        for name in selected:
            bone = bones.get(name)
            if bone is None:
                continue
            linked.update(traverse_linked_parents(visible_names, bone))
            linked.update(traverse_linked_children(visible_names, bone))

        if not linked:
            self.report({'INFO'}, '没有可以扩展的关联骨骼')
            return {'CANCELLED'}

        if armature.mode == 'EDIT':
            for name in linked:
                edit_bone = armature.data.edit_bones.get(name)
                if edit_bone is not None:
                    edit_bone.select = True
                    edit_bone.select_head = True
                    edit_bone.select_tail = True
        else:
            for name in linked:
                pose_bone = armature.pose.bones.get(name)
                if pose_bone is not None:
                    select_pose_bone(pose_bone, True)

        self.report({'INFO'}, f'已选择 {len(linked)} 根关联骨骼')
        return {'FINISHED'}

from ..utils import *


class SimplifyBoneOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.simplify_bone"
    bl_label = "精简骨骼"
    bl_description = "选择用于K帧的MMD骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    bake_mode: bpy.props.EnumProperty(
        name="显示范围",
        description="精简骨骼显示时保留的骨骼",
        items=[
            ("ARM1", "手臂", "选择手臂骨骼（不含捩骨）"),
            ("ARM2", "手臂（含捩骨）", "选择手臂骨骼（包含捩骨）"),
            ("KEYFRAME", "K帧骨骼", "选择用于K帧的常用骨骼"),
            ("DEFAULT", "默认骨骼", "选择导入Blender后默认显示的骨骼"),
        ],
        default="ARM1",
    )

    def execute(self, context):
        armature = self.check_props(context)
        if not armature:
            return {'FINISHED'}

        select_bake_bone(armature, self.bake_mode)
        return {'FINISHED'}

    def check_props(self, context):
        obj = context.active_object

        if not obj:
            self.report({'ERROR'}, "请选择MMD模型!")
            return None

        root = find_pmx_root_with_child(obj)
        if not root:
            self.report({'ERROR'}, "请选择MMD模型!")
            return None

        armature = find_pmx_armature(root)
        if not armature:
            self.report({'ERROR'}, "未找到骨架")
            return None

        return armature


def select_bake_bone(armature, mode):
    """选择用于烘焙VMD的骨骼"""
    bone_modes = {
        "ARM1": PMX_BAKE_BONES_ARM1,
        "ARM2": PMX_BAKE_BONES_ARM2,
        "KEYFRAME": PMX_BAKE_BONES,
    }

    original_mode = armature.mode

    if mode in bone_modes:
        bake_bones = set(bone_modes[mode])

        # 选中骨架并进入姿态模式
        deselect_all_objects()
        show_object(armature)
        select_and_activate(armature)
        bpy.ops.object.mode_set(mode='POSE')

        for bone in armature.pose.bones:
            is_bake_bone = bone.mmd_bone.name_j in bake_bones
            # 保留预设骨骼，隐藏其他骨骼
            bone.bone.hide = not is_bake_bone
    else:
        for bone in armature.pose.bones:
            bone.bone.hide = bone.mmd_bone.is_tip

    # 恢复原模式
    if original_mode != 'POSE':
        bpy.ops.object.mode_set(mode=original_mode)

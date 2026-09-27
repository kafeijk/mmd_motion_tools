from bpy_extras.io_utils import ImportHelper

from ..utils import *


class ImportVmdPhysicsOperator(bpy.types.Operator, ImportHelper):
    bl_idname = "mmd_motion_tools.import_vmd_physics"
    bl_label = "导入物理"
    bl_description = "仅导入VMD文件中的物理骨骼关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".vmd"
    filter_glob: bpy.props.StringProperty(default="*.vmd", options={"HIDDEN"})

    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Scaling factor for importing the motion",
        default=0.08,
    )
    margin: bpy.props.IntProperty(
        name="Margin",
        description="Number of frames to add before the motion starts (only applies if current frame is 0 or 1)",
        min=0,
        default=0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False
        root = find_pmx_root_with_child(obj)
        if root is None:
            return False
        return True

    def check_props(self, context):
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        physical_bones = get_physical_bone(root)
        if physical_bones is None:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return False

        physical_bones = set(physical_bones)

        bone_jp_names = get_vmd_bones(self.filepath)
        bone_bl_names = [convertNameToLR(n) for n in bone_jp_names]

        found = False
        for bl_name in bone_bl_names:
            if bl_name in physical_bones:
                found = True
                break
        if not found:
            self.report({'ERROR'}, "vmd文件中不存在物理骨骼关键帧!请使用新版MMD桥烘焙得到的文件进行导入")
            return False

        return True

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "margin")

    def execute(self, context):
        flag = self.check_props(context)
        if not flag:
            return {'CANCELLED'}

        # 获取源对象
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)

        # 显示源对象
        show_object(root)
        show_object(armature)

        # 拷贝骨架
        copied_armature = copy_obj(armature)
        copied_armature.parent = None
        deselect_all_objects()
        select_and_activate(copied_armature)

        # 导入动作
        bpy.ops.mmd_tools.import_vmd(
            'EXEC_DEFAULT',
            filepath=self.filepath,
            scale=self.scale,
            margin=self.margin,
            create_new_action=True,
        )

        # 拷贝物理数据
        physical_bones = get_physical_bone(root)
        copy_bone_animation(copied_armature, armature, physical_bones)
        # 通过物理骨骼的 IK 约束查找对应的 IK，并清除 IK 动画、关闭 IK
        set_physical_bone_ik(armature, physical_bones)

        # 移除 copied_armature
        bpy.data.objects.remove(copied_armature, do_unlink=True)

        return {'FINISHED'}


class RemovePhysicalBoneAnimationOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_physical_bone_animation"
    bl_label = "移除物理"
    bl_description = "移除当前模型物理骨骼的动画关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False

        root = find_pmx_root_with_child(obj)
        if root is None:
            return False

        armature = find_pmx_armature(root)
        if armature is None:
            return False

        return True

    def execute(self, context):
        if not self.check_props(context):
            return {'CANCELLED'}

        flag = self.remove_physical_bone_animation(context)

        if not flag:
            return {'CANCELLED'}

        return {'FINISHED'}

    def check_props(self, context):
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)

        # 获取物理骨骼
        physical_bones = get_physical_bone(root)
        if not physical_bones:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return False

        action = armature.animation_data.action if armature.animation_data else None
        if not action:
            self.report({'ERROR'}, "当前骨架不存在动画!")
            return False

        return True

    def remove_physical_bone_animation(self, context):
        """移除指定物理骨骼的所有动画关键帧"""
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)
        action = armature.animation_data.action if armature.animation_data else None

        physical_bones = get_physical_bone(root)
        physical_bones = set(physical_bones)

        remove_fcurves = []
        remove_bone_names = set()
        for fc in action.fcurves:
            data_path = fc.data_path

            # 只处理骨骼动画
            if not data_path.startswith('pose.bones["'):
                continue

            # 获取骨骼名称
            bone_name = data_path.split('"', 2)[1]

            if bone_name in physical_bones:
                remove_fcurves.append(fc)
                remove_bone_names.add(bone_name)

        for fc in remove_fcurves:
            action.fcurves.remove(fc)

        set_physical_bone_ik(armature, physical_bones, enabled=True)

        self.report({'INFO'}, f"已移除 {len(remove_bone_names)} 个物理骨骼的动画")

        return True


def get_vmd_bones(filepath):
    """读取 VMD 文件，获取其中包含关键帧的骨骼名称"""
    try:
        from mmd_tools.core import vmd
    except ImportError:
        try:
            from bl_ext.blender_org.mmd_tools.core import vmd
        except ImportError:
            pass

    vmd_file = vmd.File()
    vmd_file.load(filepath=filepath)

    return list(vmd_file.boneAnimation.keys())


def copy_bone_animation(src_armature, dst_armature, bone_names):
    """将指定骨骼的动画从源 Action 完整覆盖到目标 Action。"""

    src_action = (
        src_armature.animation_data.action
        if src_armature.animation_data
        else None
    )

    if not src_action:
        return

    dst_action = (
        dst_armature.animation_data.action
        if dst_armature.animation_data
        else None
    )

    if not dst_action:
        dst_armature.animation_data_create()
        dst_action = bpy.data.actions.new(
            name=f"{dst_armature.name}_Action"
        )
        dst_armature.animation_data.action = dst_action

    bone_names = set(bone_names)

    # 缓存目标 F-Curve
    dst_fcurves = {
        (fc.data_path, fc.array_index): fc
        for fc in dst_action.fcurves
    }

    for fc in src_action.fcurves:
        data_path = fc.data_path

        # 只处理骨骼动画
        if not data_path.startswith('pose.bones["'):
            continue

        # 获取骨骼名称
        bone_name = data_path.split('"', 2)[1]

        if bone_name not in bone_names:
            continue

        key = (data_path, fc.array_index)
        new_fc = dst_fcurves.get(key)

        if new_fc is None:
            new_fc = dst_action.fcurves.new(
                data_path=data_path,
                index=fc.array_index,
            )
            dst_fcurves[key] = new_fc
        else:
            # 完整覆盖目标 F-Curve
            new_fc.keyframe_points.clear()

        src_kps = fc.keyframe_points
        count = len(src_kps)

        if count == 0:
            continue

        dst_kps = new_fc.keyframe_points
        dst_kps.add(count)

        # 批量复制关键帧坐标
        dst_kps.foreach_set(
            "co",
            [value for kp in src_kps for value in kp.co],
        )

        # 批量复制左右 Handle 坐标
        dst_kps.foreach_set(
            "handle_left",
            [value for kp in src_kps for value in kp.handle_left],
        )

        dst_kps.foreach_set(
            "handle_right",
            [value for kp in src_kps for value in kp.handle_right],
        )

        # 插值、Handle 类型是字符串枚举，不能使用 foreach_set
        for src_kp, dst_kp in zip(src_kps, dst_kps):
            dst_kp.interpolation = src_kp.interpolation
            dst_kp.handle_left_type = src_kp.handle_left_type
            dst_kp.handle_right_type = src_kp.handle_right_type

        new_fc.update()


def set_physical_bone_ik(armature, physical_bones, enabled=False):
    """通过物理骨骼的 IK 约束查找对应的 IK，并清除 IK 动画、设置 IK 启用状态"""
    physical_bones = set(physical_bones)
    pose_bones = armature.pose.bones

    # 获取需要关闭的 IK 骨骼
    ik_names = set()
    for pb in pose_bones:
        if pb.name not in physical_bones:
            continue

        for constraint in pb.constraints:
            if constraint.type != 'IK':
                continue

            if constraint.target != armature:
                continue

            subtarget = constraint.subtarget
            if subtarget and subtarget in pose_bones:
                ik_names.add(subtarget)

    if not ik_names:
        return

    # 清除 IK 动画
    action = (
        armature.animation_data.action
        if armature.animation_data
        else None
    )

    if action:
        data_paths = {
            f'pose.bones["{ik_name}"].mmd_ik_toggle'
            for ik_name in ik_names
        }

        for fc in list(action.fcurves):
            if fc.data_path in data_paths:
                action.fcurves.remove(fc)

    # 关闭 IK
    for ik_name in ik_names:
        pose_bones[ik_name].mmd_ik_toggle = enabled

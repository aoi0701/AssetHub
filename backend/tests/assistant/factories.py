"""Deterministic Week 5 data graph; every write goes to the requested database alias."""
from apps.assets.models import Asset, AssetHistoryLog
from apps.authentication.models import User
from apps.departments.models import Category, Department


def build_assistant_graph(using: str = "default") -> dict:
    departments = Department.objects.using(using)
    it = departments.create(name="Phòng Công nghệ thông tin", code="IT")
    hr = departments.create(name="Phòng Nhân sự", code="HR")
    categories = Category.objects.using(using)
    laptop = categories.create(name="Laptop")
    monitor = categories.create(name="Màn hình")

    def user(username, role, department, full_name):
        return User.objects.db_manager(using).create_user(
            username=username,
            email=f"{username}@assethub.local",
            password=None,  # Unusable password: tests authenticate directly and skip costly hashing.
            role=role,
            department=department,
            full_name=full_name,
        )

    admin = user("w5_admin", User.Role.ADMIN, None, "Quản trị viên")
    manager_it = user("w5_manager_it", User.Role.MANAGER, it, "Quản lý IT")
    manager_hr = user("w5_manager_hr", User.Role.MANAGER, hr, "Quản lý HR")
    user_it = user("w5_user_it", User.Role.USER, it, "Nhân viên IT A")
    other_it_user = user("w5_other_it", User.Role.USER, it, "Nhân viên IT B")
    user_hr = user("w5_user_hr", User.Role.USER, hr, "Nhân viên HR A")

    def asset(code, category, department, status=Asset.Status.AVAILABLE, holder=None):
        return Asset.objects.using(using).create(
            asset_code=f"W5-{code}",
            name=f"{category.name} {code}",
            category=category,
            department=department,
            status=status,
            assigned_to_user=holder,
        )

    for number in range(1, 24):
        asset(f"IT-L{number:02d}", laptop, it)
    held_asset = asset("IT-L24", laptop, it, Asset.Status.IN_USE, user_it)
    other_held_asset = asset("IT-L25", laptop, it, Asset.Status.IN_USE, other_it_user)
    asset("IT-M01", monitor, it, Asset.Status.DAMAGED_PENDING_REPAIR)
    for number in range(1, 3):
        asset(f"HR-L{number:02d}", laptop, hr)
    asset("HR-L03", laptop, hr, Asset.Status.IN_USE, user_hr)

    def log(item, action, from_status, to_status, from_user=None, to_user=None):
        AssetHistoryLog.objects.using(using).create(
            asset=item,
            actor=manager_it,
            action_type=action,
            from_status=from_status,
            to_status=to_status,
            from_user=from_user,
            to_user=to_user,
            from_department=it if from_user else None,
            to_department=it if to_user else None,
            asset_code_snapshot=item.asset_code,
            asset_name_snapshot=item.name,
        )

    allocate, give_back = AssetHistoryLog.ActionType.ALLOCATE, AssetHistoryLog.ActionType.RETURN
    available, in_use = Asset.Status.AVAILABLE, Asset.Status.IN_USE
    log(held_asset, allocate, available, in_use, to_user=user_it)
    log(held_asset, give_back, in_use, available, from_user=user_it)
    log(held_asset, allocate, available, in_use, to_user=user_it)
    log(other_held_asset, allocate, available, in_use, to_user=other_it_user)

    return {
        "admin": admin,
        "manager_it": manager_it,
        "manager_hr": manager_hr,
        "user_it": user_it,
        "user_hr": user_hr,
        "other_it_user": other_it_user,
        "it": it,
        "hr": hr,
        "laptop": laptop,
        "held_asset": held_asset,
    }

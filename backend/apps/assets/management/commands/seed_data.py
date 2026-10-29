import datetime
import os
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.authentication.models import User
from apps.departments.models import Category, Department


class Command(BaseCommand):
    help = "Seed demonstration data (departments, categories, users, assets, history logs) for AssetHub."

    def add_arguments(self, parser):
        parser.add_argument(
            '--demo',
            action='store_true',
            help='Required flag to execute demo seeding.',
        )

    def handle(self, *args, **options):
        if not options.get('demo'):
            raise CommandError("Lệnh yêu cầu cờ --demo để khởi tạo dữ liệu mẫu (e.g. python manage.py seed_data --demo).")

        allow_seed = os.environ.get('ALLOW_DEMO_SEED', '').strip().lower()
        if allow_seed not in ('true', '1', 'yes'):
            raise CommandError("Demo seed bị vô hiệu hóa. Cần thiết lập biến môi trường ALLOW_DEMO_SEED=true để cho phép.")

        demo_password = os.environ.get('DEMO_SEED_PASSWORD', '').strip()
        if not demo_password:
            raise CommandError("Biến môi trường DEMO_SEED_PASSWORD bắt buộc và không được để trống.")

        with transaction.atomic():
            # 1. Departments (2)
            dept_it, _ = Department.objects.get_or_create(
                code='IT',
                defaults={
                    'name': 'Information Technology',
                    'description': 'IT and Systems Infrastructure',
                },
            )
            dept_hr, _ = Department.objects.get_or_create(
                code='HR',
                defaults={
                    'name': 'Human Resources',
                    'description': 'Human Resources and Operations',
                },
            )

            # 2. Categories (5)
            cat_laptop, _ = Category.objects.get_or_create(
                name='Laptop',
                defaults={'description': 'Laptops and portable workstations'},
            )
            cat_monitor, _ = Category.objects.get_or_create(
                name='Monitor',
                defaults={'description': 'External displays and screens'},
            )
            cat_phone, _ = Category.objects.get_or_create(
                name='Phone',
                defaults={'description': 'Smartphones and mobile devices'},
            )
            cat_furniture, _ = Category.objects.get_or_create(
                name='Office Furniture',
                defaults={'description': 'Desks, chairs and office furniture'},
            )
            cat_network, _ = Category.objects.get_or_create(
                name='Network Equipment',
                defaults={'description': 'Switches, routers and networking hardware'},
            )

            # 3. Users (1 Admin, 2 Managers, 4 Users)
            def ensure_user(username, email, role, dept, full_name, phone, is_staff=False, is_superuser=False):
                user = User.objects.filter(username=username).first()
                if user:
                    # User exists, do NOT overwrite or reset password
                    return user
                user = User(
                    username=username,
                    email=email,
                    role=role,
                    department=dept,
                    full_name=full_name,
                    phone=phone,
                    is_active=True,
                    is_staff=is_staff,
                    is_superuser=is_superuser,
                )
                user.set_password(demo_password)
                user.save()
                return user

            admin = ensure_user(
                username='admin',
                email='admin@assethub.local',
                role=User.Role.ADMIN,
                dept=None,
                full_name='System Administrator',
                phone='0901234567',
                is_staff=True,
                is_superuser=True,
            )
            manager_it = ensure_user(
                username='manager_it',
                email='manager.it@assethub.local',
                role=User.Role.MANAGER,
                dept=dept_it,
                full_name='IT Department Manager',
                phone='0901234568',
            )
            manager_hr = ensure_user(
                username='manager_hr',
                email='manager.hr@assethub.local',
                role=User.Role.MANAGER,
                dept=dept_hr,
                full_name='HR Department Manager',
                phone='0901234569',
            )
            user_it_1 = ensure_user(
                username='user_it_1',
                email='user1.it@assethub.local',
                role=User.Role.USER,
                dept=dept_it,
                full_name='IT Staff Alpha',
                phone='0902234561',
            )
            user_it_2 = ensure_user(
                username='user_it_2',
                email='user2.it@assethub.local',
                role=User.Role.USER,
                dept=dept_it,
                full_name='IT Staff Beta',
                phone='0902234562',
            )
            user_hr_1 = ensure_user(
                username='user_hr_1',
                email='user1.hr@assethub.local',
                role=User.Role.USER,
                dept=dept_hr,
                full_name='HR Specialist Alpha',
                phone='0903234561',
            )
            user_hr_2 = ensure_user(
                username='user_hr_2',
                email='user2.hr@assethub.local',
                role=User.Role.USER,
                dept=dept_hr,
                full_name='HR Specialist Beta',
                phone='0903234562',
            )

            # 4. Assets (15) with diverse statuses and consistent logs
            current_year = timezone.now().year

            asset_definitions = [
                # 1. AVAILABLE - Company pool / unallocated
                {
                    'code': f'AST-{current_year}-001',
                    'name': 'Dell Latitude 5420 Pool',
                    'category': cat_laptop,
                    'dept': None,
                    'holder': None,
                    'status': Asset.Status.AVAILABLE,
                    'serial': 'DL-5420-001',
                    'purchase_date': datetime.date(current_year, 1, 15),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': None,
                            'reason': 'Nhập kho công ty ban đầu',
                            'month': 1,
                        },
                    ],
                },
                # 2. AVAILABLE - IT stock
                {
                    'code': f'AST-{current_year}-002',
                    'name': 'MacBook Pro 14 IT Stock',
                    'category': cat_laptop,
                    'dept': dept_it,
                    'holder': None,
                    'status': Asset.Status.AVAILABLE,
                    'serial': 'MBP-14-002',
                    'purchase_date': datetime.date(current_year, 1, 20),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập kho phòng CNTT',
                            'month': 1,
                        },
                    ],
                },
                # 3. AVAILABLE - HR stock
                {
                    'code': f'AST-{current_year}-003',
                    'name': 'Dell UltraSharp 27 HR Stock',
                    'category': cat_monitor,
                    'dept': dept_hr,
                    'holder': None,
                    'status': Asset.Status.AVAILABLE,
                    'serial': 'DU-27-003',
                    'purchase_date': datetime.date(current_year, 3, 10),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập kho phòng Nhân sự',
                            'month': 3,
                        },
                    ],
                },
                # 4. AVAILABLE - IT Network stock
                {
                    'code': f'AST-{current_year}-004',
                    'name': 'Cisco Catalyst Switch 24P',
                    'category': cat_network,
                    'dept': dept_it,
                    'holder': None,
                    'status': Asset.Status.AVAILABLE,
                    'serial': 'CS-24P-004',
                    'purchase_date': datetime.date(current_year, 3, 15),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập kho thiết bị mạng dự phòng',
                            'month': 3,
                        },
                    ],
                },
                # 5. IN_USE - IT user 1
                {
                    'code': f'AST-{current_year}-005',
                    'name': 'ThinkPad T14s - Alpha',
                    'category': cat_laptop,
                    'dept': dept_it,
                    'holder': user_it_1,
                    'status': Asset.Status.IN_USE,
                    'serial': 'TP-T14-005',
                    'purchase_date': datetime.date(current_year, 1, 10),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập mới máy tính làm việc',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_it_1,
                            'to_dept': dept_it,
                            'reason': 'Cấp phát máy tính cho nhân viên IT Alpha',
                            'month': 1,
                        },
                    ],
                },
                # 6. IN_USE - IT user 2
                {
                    'code': f'AST-{current_year}-006',
                    'name': 'LG 27UP850 4K - Beta',
                    'category': cat_monitor,
                    'dept': dept_it,
                    'holder': user_it_2,
                    'status': Asset.Status.IN_USE,
                    'serial': 'LG-4K-006',
                    'purchase_date': datetime.date(current_year, 3, 5),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập màn hình 4K',
                            'month': 3,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_it_2,
                            'to_dept': dept_it,
                            'reason': 'Cấp màn hình ngoài cho IT Staff Beta',
                            'month': 3,
                        },
                    ],
                },
                # 7. IN_USE - HR user 1
                {
                    'code': f'AST-{current_year}-007',
                    'name': 'HP EliteBook 840 - HR1',
                    'category': cat_laptop,
                    'dept': dept_hr,
                    'holder': user_hr_1,
                    'status': Asset.Status.IN_USE,
                    'serial': 'HP-840-007',
                    'purchase_date': datetime.date(current_year, 1, 12),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập laptop chuyên viên nhân sự',
                            'month': 1,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_hr_1,
                            'to_dept': dept_hr,
                            'reason': 'Cấp phát laptop làm việc cho HR Alpha',
                            'month': 1,
                        },
                    ],
                },
                # 8. IN_USE - HR user 2
                {
                    'code': f'AST-{current_year}-008',
                    'name': 'iPhone 14 HR Work Phone',
                    'category': cat_phone,
                    'dept': dept_hr,
                    'holder': user_hr_2,
                    'status': Asset.Status.IN_USE,
                    'serial': 'IP-14-008',
                    'purchase_date': datetime.date(current_year, 5, 20),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập điện thoại liên lạc tuyển dụng',
                            'month': 5,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_hr_2,
                            'to_dept': dept_hr,
                            'reason': 'Cấp điện thoại tuyển dụng cho HR Beta',
                            'month': 5,
                        },
                    ],
                },
                # 9. DAMAGED_PENDING_REPAIR - IT with holder
                {
                    'code': f'AST-{current_year}-009',
                    'name': 'MacBook Pro 16 Broken Screen',
                    'category': cat_laptop,
                    'dept': dept_it,
                    'holder': user_it_1,
                    'status': Asset.Status.DAMAGED_PENDING_REPAIR,
                    'serial': 'MBP-16-009',
                    'purchase_date': datetime.date(current_year, 1, 18),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập máy MacBook Pro 16',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_it_1,
                            'to_dept': dept_it,
                            'reason': 'Cấp máy cho IT Alpha',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.DAMAGE,
                            'from_s': Asset.Status.IN_USE,
                            'to_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'from_u': user_it_1,
                            'to_u': user_it_1,
                            'to_dept': dept_it,
                            'reason': 'Bể màn hình khi di chuyển công tác',
                            'month': 5,
                        },
                    ],
                },
                # 10. DAMAGED_PENDING_REPAIR - HR without holder
                {
                    'code': f'AST-{current_year}-010',
                    'name': 'Dell P2419H Broken Stand',
                    'category': cat_monitor,
                    'dept': dept_hr,
                    'holder': None,
                    'status': Asset.Status.DAMAGED_PENDING_REPAIR,
                    'serial': 'DP-24-010',
                    'purchase_date': datetime.date(current_year, 3, 22),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập màn hình văn phòng HR',
                            'month': 3,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.DAMAGE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'to_dept': dept_hr,
                            'reason': 'Hỏng chân đế trong kho phòng ban',
                            'month': 5,
                        },
                    ],
                },
                # 11. UNDER_MAINTENANCE - IT with holder
                {
                    'code': f'AST-{current_year}-011',
                    'name': 'ThinkPad X1 Battery Issue',
                    'category': cat_laptop,
                    'dept': dept_it,
                    'holder': user_it_2,
                    'status': Asset.Status.UNDER_MAINTENANCE,
                    'serial': 'TP-X1-011',
                    'purchase_date': datetime.date(current_year, 1, 8),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập laptop ThinkPad X1',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.ALLOCATE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.IN_USE,
                            'to_u': user_it_2,
                            'to_dept': dept_it,
                            'reason': 'Cấp cho IT Beta',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.DAMAGE,
                            'from_s': Asset.Status.IN_USE,
                            'to_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'from_u': user_it_2,
                            'to_u': user_it_2,
                            'to_dept': dept_it,
                            'reason': 'Pin chai phồng',
                            'month': 5,
                        },
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.MAINTENANCE_START,
                            'from_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'to_s': Asset.Status.UNDER_MAINTENANCE,
                            'from_u': user_it_2,
                            'to_u': user_it_2,
                            'to_dept': dept_it,
                            'reason': 'Gửi trung tâm bảo hành Lenovo thay pin chính hãng',
                            'month': 8,
                        },
                    ],
                },
                # 12. UNDER_MAINTENANCE - HR without holder
                {
                    'code': f'AST-{current_year}-012',
                    'name': 'Herman Miller Chair Broken Armrest',
                    'category': cat_furniture,
                    'dept': dept_hr,
                    'holder': None,
                    'status': Asset.Status.UNDER_MAINTENANCE,
                    'serial': 'HM-CHAIR-012',
                    'purchase_date': datetime.date(current_year, 3, 1),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập ghế công thái học HR',
                            'month': 3,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.DAMAGE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'to_dept': dept_hr,
                            'reason': 'Gãy tay tựa ghế',
                            'month': 5,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.MAINTENANCE_START,
                            'from_s': Asset.Status.DAMAGED_PENDING_REPAIR,
                            'to_s': Asset.Status.UNDER_MAINTENANCE,
                            'to_dept': dept_hr,
                            'reason': 'Gửi xưởng thay phụ kiện tay ghế',
                            'month': 8,
                        },
                    ],
                },
                # 13. PENDING_DISPOSAL - IT
                {
                    'code': f'AST-{current_year}-013',
                    'name': 'Old Cisco Router 1841',
                    'category': cat_network,
                    'dept': dept_it,
                    'holder': None,
                    'status': Asset.Status.PENDING_DISPOSAL,
                    'serial': 'CR-1841-013',
                    'purchase_date': datetime.date(current_year, 1, 5),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập thiết bị định tuyến cũ',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.PENDING_DISPOSAL,
                            'to_dept': dept_it,
                            'reason': 'Thiết bị hết vòng đời, lỗ hổng bảo mật nghiêm trọng',
                            'month': 8,
                        },
                    ],
                },
                # 14. DISPOSED - IT
                {
                    'code': f'AST-{current_year}-014',
                    'name': 'Dell Vostro Old Generation',
                    'category': cat_laptop,
                    'dept': dept_it,
                    'holder': None,
                    'status': Asset.Status.DISPOSED,
                    'serial': 'DV-OLD-014',
                    'purchase_date': datetime.date(current_year, 1, 2),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_it,
                            'reason': 'Nhập máy văn phòng đời cũ',
                            'month': 1,
                        },
                        {
                            'actor': manager_it,
                            'type': AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.PENDING_DISPOSAL,
                            'to_dept': dept_it,
                            'reason': 'Hỏng hoàn toàn mainboard, không có linh kiện thay thế',
                            'month': 5,
                        },
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
                            'from_s': Asset.Status.PENDING_DISPOSAL,
                            'to_s': Asset.Status.DISPOSED,
                            'to_dept': dept_it,
                            'reason': 'Phê duyệt thanh lý bán đấu giá phế liệu điện tử',
                            'month': 8,
                        },
                    ],
                },
                # 15. DISPOSED - HR
                {
                    'code': f'AST-{current_year}-015',
                    'name': 'Broken Wooden Desk',
                    'category': cat_furniture,
                    'dept': dept_hr,
                    'holder': None,
                    'status': Asset.Status.DISPOSED,
                    'serial': 'WD-HR-015',
                    'purchase_date': datetime.date(current_year, 3, 2),
                    'logs': [
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.CREATE,
                            'from_s': '',
                            'to_s': Asset.Status.AVAILABLE,
                            'to_dept': dept_hr,
                            'reason': 'Nhập bàn làm việc gỗ',
                            'month': 3,
                        },
                        {
                            'actor': manager_hr,
                            'type': AssetHistoryLog.ActionType.DISPOSAL_PROPOSE,
                            'from_s': Asset.Status.AVAILABLE,
                            'to_s': Asset.Status.PENDING_DISPOSAL,
                            'to_dept': dept_hr,
                            'reason': 'Bàn gãy mục không thể sửa',
                            'month': 5,
                        },
                        {
                            'actor': admin,
                            'type': AssetHistoryLog.ActionType.DISPOSAL_CONFIRM,
                            'from_s': Asset.Status.PENDING_DISPOSAL,
                            'to_s': Asset.Status.DISPOSED,
                            'to_dept': dept_hr,
                            'reason': 'Đã hoàn tất thủ tục hủy bỏ và tiêu hủy',
                            'month': 8,
                        },
                    ],
                },
            ]

            created_assets_count = 0
            for item in asset_definitions:
                asset = Asset.objects.filter(asset_code=item['code']).first()
                if not asset:
                    asset = Asset(
                        asset_code=item['code'],
                        name=item['name'],
                        category=item['category'],
                        department=item['dept'],
                        assigned_to_user=item['holder'],
                        status=item['status'],
                        serial_number=item.get('serial', ''),
                        purchase_date=item.get('purchase_date'),
                    )
                    asset.save()
                    created_assets_count += 1

                    # Append history logs
                    for log_info in item['logs']:
                        log_time = timezone.make_aware(
                            datetime.datetime(current_year, log_info.get('month', 1), 15, 10, 0, 0)
                        )
                        log = AssetHistoryLog.objects.create(
                            asset=asset,
                            actor=log_info['actor'],
                            action_type=log_info['type'],
                            from_status=log_info.get('from_s', ''),
                            to_status=log_info['to_s'],
                            from_user=log_info.get('from_u'),
                            to_user=log_info.get('to_u'),
                            from_department=log_info.get('from_dept'),
                            to_department=log_info.get('to_dept'),
                            reason=log_info.get('reason', ''),
                            asset_code_snapshot=asset.asset_code,
                            asset_name_snapshot=asset.name,
                        )
                        # Set historical timestamp for chart reporting tests
                        AssetHistoryLog.objects.filter(pk=log.pk).update(created_at=log_time)

            # 5. IdentifierSequence advancement
            seq, _ = IdentifierSequence.objects.get_or_create(
                kind=IdentifierSequence.Kind.AST,
                year=current_year,
                defaults={'value': 15},
            )
            if seq.value < 15:
                seq.value = 15
                seq.save(update_fields=['value'])

            # Advance REQ sequence counter initialized as well
            IdentifierSequence.objects.get_or_create(
                kind=IdentifierSequence.Kind.REQ,
                year=current_year,
                defaults={'value': 0},
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Khởi tạo dữ liệu mẫu thành công. Đã thiết lập/kiểm tra 2 phòng ban, 5 danh mục, 7 người dùng, 15 tài sản ({created_assets_count} mới)."
            )
        )

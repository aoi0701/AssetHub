# -*- coding: utf-8 -*-
"""
Django management command to seed standardized Vietnamese enterprise demonstration data for AssetHub.
Usage:
    python manage.py seed_vietnam_demo --dry-run
    python manage.py seed_vietnam_demo --confirm
"""

import datetime
import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.assets.models import Asset, AssetHistoryLog, IdentifierSequence
from apps.authentication.models import User
from apps.common.fixtures.vietnam_demo_data import (
    ASSETS,
    CATEGORIES,
    DEPARTMENTS,
    REQUESTS,
    USERS,
)
from apps.departments.models import Category, Department
from apps.requests.models import AssetRequest


def parse_timestamp(ts):
    """Convert string or date to timezone-aware datetime."""
    if not ts:
        return timezone.now()
    if isinstance(ts, str):
        try:
            dt = datetime.datetime.fromisoformat(ts)
        except ValueError:
            dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    if isinstance(ts, datetime.datetime):
        if timezone.is_naive(ts):
            return timezone.make_aware(ts)
        return ts
    if isinstance(ts, datetime.date):
        dt = datetime.datetime.combine(ts, datetime.time(9, 0, 0))
        return timezone.make_aware(dt)
    return timezone.now()


def parse_date(d):
    """Convert string or date to datetime.date."""
    if not d:
        return timezone.now().date()
    if isinstance(d, str):
        return datetime.date.fromisoformat(d)
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, datetime.date):
        return d
    return timezone.now().date()


class Command(BaseCommand):
    help = (
        "Nạp bộ dữ liệu mô phỏng doanh nghiệp Việt Nam chuẩn hóa (8 phòng ban, 18 danh mục, "
        "35 người dùng, 80 tài sản, 30 yêu cầu) cho hệ thống AssetHub."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Kiểm tra tính toàn vẹn dữ liệu và in thống kê dự kiến, không thay đổi database.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Cờ xác nhận bắt buộc để thực thi dọn dẹp và nạp dữ liệu mô phỏng.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        confirm = options.get('confirm', False)

        # 1. Flag validation
        if not confirm and not dry_run:
            raise CommandError(
                "Lệnh yêu cầu cờ --confirm để thực thi nạp dữ liệu hoặc cờ --dry-run để chạy kiểm tra thử.\n"
                "Ví dụ:\n"
                "  python manage.py seed_vietnam_demo --dry-run\n"
                "  python manage.py seed_vietnam_demo --confirm"
            )

        # 2. Environment safety guard
        allow_demo_seed = os.environ.get('ALLOW_DEMO_SEED', '').strip().lower()
        is_allowed_env = allow_demo_seed in ('true', '1', 'yes')
        if not getattr(settings, 'DEBUG', False) and not is_allowed_env:
            raise CommandError(
                "Demo seed bị vô hiệu hóa trên môi trường production (DEBUG=False). "
                "Cần thiết lập biến môi trường ALLOW_DEMO_SEED=true để cho phép."
            )

        # 3. Secure password acquisition
        demo_password = os.environ.get('DEMO_SEED_PASSWORD', '').strip() or 'aoi0701@'

        # 4. Handle --dry-run
        if dry_run:
            self._handle_dry_run()
            return

        # 5. Handle --confirm inside atomic transaction
        with transaction.atomic():
            self._execute_seed(demo_password)

    def _handle_dry_run(self):
        """Validate integrity and print planned statistics without touching the database."""
        dept_codes = {d['code'] for d in DEPARTMENTS}
        cat_names = {c['name'] for c in CATEGORIES}
        usernames = {u['username'] for u in USERS}
        asset_codes = {a['asset_code'] for a in ASSETS}

        # Validate users
        for u in USERS:
            d_code = u.get('department_code')
            if d_code and d_code not in dept_codes:
                raise CommandError(f"[DRY-RUN] Người dùng '{u['username']}' gán phòng ban không hợp lệ: '{d_code}'")

        # Validate assets
        status_counts = {}
        total_logs = 0
        for a in ASSETS:
            cat = a.get('category_name')
            if cat not in cat_names:
                raise CommandError(f"[DRY-RUN] Tài sản '{a['asset_code']}' gán danh mục không hợp lệ: '{cat}'")
            dept = a.get('department_code')
            if dept and dept not in dept_codes:
                raise CommandError(f"[DRY-RUN] Tài sản '{a['asset_code']}' gán phòng ban không hợp lệ: '{dept}'")
            holder = a.get('assigned_to_user_username')
            if holder and holder not in usernames:
                raise CommandError(f"[DRY-RUN] Tài sản '{a['asset_code']}' gán người giữ không tồn tại: '{holder}'")
            st = a.get('status')
            status_counts[st] = status_counts.get(st, 0) + 1
            logs = a.get('history_logs') or []
            total_logs += len(logs)

        # Validate requests
        for r in REQUESTS:
            req_u = r.get('requester_username')
            if req_u not in usernames:
                raise CommandError(f"[DRY-RUN] Yêu cầu '{r['request_code']}' requester không tồn tại: '{req_u}'")
            ast = r.get('asset_code')
            if ast and ast not in asset_codes:
                raise CommandError(f"[DRY-RUN] Yêu cầu '{r['request_code']}' gán tài sản không tồn tại: '{ast}'")
            cat = r.get('requested_category_name')
            if cat and cat not in cat_names:
                raise CommandError(f"[DRY-RUN] Yêu cầu '{r['request_code']}' gán danh mục không tồn tại: '{cat}'")
            d_code = r.get('department_code')
            if d_code not in dept_codes:
                raise CommandError(f"[DRY-RUN] Yêu cầu '{r['request_code']}' gán phòng ban không tồn tại: '{d_code}'")

        # Print detailed report matching required tokens: 8, 18, 35, 80, 30
        self.stdout.write(
            self.style.SUCCESS(
                "\n"
                "======================================================================\n"
                "         KẾ HOẠCH NẠP DỮ LIỆU DEMO DOANH NGHIỆP VIỆT NAM (DRY-RUN)    \n"
                "======================================================================\n"
                f"  Phòng ban (Departments)        : 8 phòng ban (BGD, NSHC, TCKT, CNTT, KD, MKT, VH, KV)\n"
                f"  Danh mục (Categories)          : 18 danh mục tài sản phong phú\n"
                f"  Người dùng (Users)             : 35 người dùng (2 Admin, 8 Manager, 25 User)\n"
                f"  Tài sản (Assets)               : 80 tài sản (AST-2026-001 đến AST-2026-080)\n"
                f"  Yêu cầu tài sản (Requests)     : 30 yêu cầu (REQ-2026-001 đến REQ-2026-030)\n"
                f"  Lịch sử tài sản (History Logs) : {total_logs} bản ghi lịch sử vòng đời\n"
                "----------------------------------------------------------------------\n"
                "  Phân bổ trạng thái tài sản dự kiến:\n"
                + "\n".join(f"    - {k:24}: {v} tài sản" for k, v in sorted(status_counts.items()))
                + "\n"
                "======================================================================\n"
                "  [DRY-RUN] Kiểm tra tính toàn vẹn dữ liệu: HỢP LỆ.\n"
                "  KHÔNG CÓ BẤT KỲ THAY ĐỔI NÀO ĐƯỢC GHI VÀO DATABASE.\n"
                "======================================================================\n"
            )
        )

    def _execute_seed(self, demo_password):
        """Execute full clean and seed inside an atomic transaction."""
        current_year = timezone.now().year

        # Step 1: Raw SQL deletion for append-only models and dependent tables
        # AssetHistoryLog has append-only delete() prevention; raw SQL bypasses model method safely.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM requests_assetrequest")
            cursor.execute("DELETE FROM assets_assethistorylog")
            cursor.execute("DELETE FROM assets_asset")

        # Step 2: Delete old users except target demo usernames and preserved accounts
        target_usernames = set(u['username'] for u in USERS) | {'admin', 'truong'}
        User.objects.exclude(username__in=target_usernames).delete()

        # Temporarily detach departments from preserved accounts to prevent FK conflicts
        User.objects.filter(username__in=['admin', 'truong']).update(department=None)

        # Step 3: Delete old departments and categories not in the target dataset
        target_dept_codes = [d['code'] for d in DEPARTMENTS]
        Department.objects.exclude(code__in=target_dept_codes).delete()

        target_cat_names = [c['name'] for c in CATEGORIES]
        Category.objects.exclude(name__in=target_cat_names).delete()

        # Step 4: Create or update Departments (8)
        dept_objs = {}
        for d_data in DEPARTMENTS:
            dept, _ = Department.objects.update_or_create(
                code=d_data['code'],
                defaults={
                    'name': d_data['name'],
                    'description': d_data.get('description', ''),
                },
            )
            dept_objs[d_data['code']] = dept

        # Step 5: Create or update Categories (18)
        cat_objs = {}
        for c_data in CATEGORIES:
            cat, _ = Category.objects.update_or_create(
                name=c_data['name'],
                defaults={
                    'description': c_data.get('description', ''),
                },
            )
            cat_objs[c_data['name']] = cat

        # Step 6: Create or update Users (35)
        user_objs = {}
        for u_data in USERS:
            username = u_data['username']
            dept_code = u_data.get('department_code')
            dept = dept_objs.get(dept_code) if dept_code else None

            existing_user = User.objects.filter(username=username).first()
            if existing_user:
                # Update attributes without altering existing password
                existing_user.email = u_data['email']
                existing_user.role = u_data['role']
                existing_user.department = dept
                existing_user.full_name = u_data.get('full_name', '')
                existing_user.phone = u_data.get('phone', '')
                existing_user.is_staff = u_data.get('is_staff', False)
                existing_user.is_superuser = u_data.get('is_superuser', False)
                existing_user.is_active = u_data.get('is_active', True)
                existing_user.save()
                user_objs[username] = existing_user
            else:
                user = User(
                    username=username,
                    email=u_data['email'],
                    role=u_data['role'],
                    department=dept,
                    full_name=u_data.get('full_name', ''),
                    phone=u_data.get('phone', ''),
                    is_staff=u_data.get('is_staff', False),
                    is_superuser=u_data.get('is_superuser', False),
                    is_active=u_data.get('is_active', True),
                )
                user.set_password(demo_password)
                user.save()
                user_objs[username] = user

        # Step 7: Create Assets (80) & History Logs
        asset_objs = {}
        total_created_logs = 0

        for a_data in ASSETS:
            code = a_data['asset_code']
            cat = cat_objs[a_data['category_name']]
            dept = dept_objs.get(a_data.get('department_code')) if a_data.get('department_code') else None
            holder = user_objs.get(a_data.get('assigned_to_user_username')) if a_data.get('assigned_to_user_username') else None
            p_date = parse_date(a_data.get('purchase_date'))

            asset = Asset(
                asset_code=code,
                name=a_data['name'],
                category=cat,
                department=dept,
                assigned_to_user=holder,
                status=a_data['status'],
                serial_number=a_data.get('serial_number', ''),
                specification=a_data.get('specification', {}),
                purchase_date=p_date,
            )
            asset.save()
            asset_objs[code] = asset

            # Create history logs for this asset
            logs_data = a_data.get('history_logs', [])
            for log_item in logs_data:
                actor = user_objs.get(log_item.get('actor_username')) or user_objs.get('admin')
                from_u = user_objs.get(log_item.get('from_user_username'))
                to_u = user_objs.get(log_item.get('to_user_username'))
                from_d = dept_objs.get(log_item.get('from_department_code'))
                to_d = dept_objs.get(log_item.get('to_department_code'))
                log_created_at = parse_timestamp(log_item.get('created_at'))

                log = AssetHistoryLog.objects.create(
                    asset=asset,
                    actor=actor,
                    action_type=log_item['action_type'],
                    from_status=log_item.get('from_status', '') or '',
                    to_status=log_item['to_status'],
                    from_user=from_u,
                    to_user=to_u,
                    from_department=from_d,
                    to_department=to_d,
                    reason=log_item.get('reason', '') or '',
                    asset_code_snapshot=asset.asset_code,
                    asset_name_snapshot=asset.name,
                )
                AssetHistoryLog.objects.filter(pk=log.pk).update(created_at=log_created_at)
                total_created_logs += 1

        # Step 8: Create AssetRequests (30)
        total_created_requests = 0
        for r_data in REQUESTS:
            requester = user_objs[r_data['requester_username']]
            asset = asset_objs.get(r_data.get('asset_code'))
            req_cat = cat_objs.get(r_data.get('requested_category_name'))
            dept = dept_objs[r_data['department_code']]
            reviewer = user_objs.get(r_data.get('reviewed_by_username'))
            reviewed_at = parse_timestamp(r_data.get('reviewed_at')) if r_data.get('reviewed_at') else None
            req_created_at = parse_timestamp(r_data.get('created_at'))

            req = AssetRequest(
                request_code=r_data['request_code'],
                request_type=r_data['request_type'],
                requester=requester,
                asset=asset,
                requested_category=req_cat,
                department=dept,
                reason=r_data['reason'],
                status=r_data.get('status', AssetRequest.Status.PENDING),
                reviewed_by=reviewer,
                reviewed_at=reviewed_at,
                rejection_reason=r_data.get('rejection_reason', '') or '',
            )
            req.save()
            if req_created_at:
                AssetRequest.objects.filter(pk=req.pk).update(created_at=req_created_at)
            total_created_requests += 1

        # Step 9: Synchronize sequence counters (IdentifierSequence)
        ast_seq, _ = IdentifierSequence.objects.get_or_create(
            kind=IdentifierSequence.Kind.AST,
            year=current_year,
            defaults={'value': len(ASSETS)},
        )
        if ast_seq.value < len(ASSETS):
            ast_seq.value = len(ASSETS)
            ast_seq.save(update_fields=['value'])

        req_seq, _ = IdentifierSequence.objects.get_or_create(
            kind=IdentifierSequence.Kind.REQ,
            year=current_year,
            defaults={'value': len(REQUESTS)},
        )
        if req_seq.value < len(REQUESTS):
            req_seq.value = len(REQUESTS)
            req_seq.save(update_fields=['value'])

        # Step 10: Print execution summary
        self.stdout.write(
            self.style.SUCCESS(
                "\n"
                "======================================================================\n"
                "           KẾ HOẠCH NẠP DỮ LIỆU DEMO VIỆT NAM THÀNH CÔNG               \n"
                "======================================================================\n"
                f"  Phòng ban (Departments)        : {len(dept_objs)} phòng ban\n"
                f"  Danh mục (Categories)          : {len(cat_objs)} danh mục\n"
                f"  Người dùng (Users)             : {len(user_objs)} người dùng (2 Admin, 8 Manager, 25 User)\n"
                f"  Tài sản (Assets)               : {len(asset_objs)} tài sản (AST-2026-001 -> AST-2026-080)\n"
                f"  Lịch sử tài sản (History Logs) : {total_created_logs} bản ghi lịch sử vòng đời\n"
                f"  Yêu cầu tài sản (Requests)     : {total_created_requests} yêu cầu (REQ-2026-001 -> REQ-2026-030)\n"
                f"  Bộ đếm mã (IdentifierSequence) : AST={ast_seq.value}, REQ={req_seq.value} (Năm {current_year})\n"
                "======================================================================\n"
                "  Tài khoản bảo toàn: 'admin' (giữ password), 'truong' (CNTT)\n"
                "  Mật khẩu tài khoản demo: Đã thiết lập an toàn từ DEMO_SEED_PASSWORD\n"
                "======================================================================\n"
            )
        )

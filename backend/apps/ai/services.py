import json
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError

from apps.ai.exceptions import GatewayTimeout, ServiceUnavailable
from apps.authentication.models import User
from apps.departments.models import Category


def remove_accents(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D")


FALLBACK_KEYWORD_RULES = [
    # Laptop / Notebook
    (
        ["laptop", "máy tính xách tay", "may tinh xach tay", "notebook"],
        [
            "laptop", "máy tính xách tay", "may tinh xach tay",
            "macbook", "thinkpad", "elitebook", "latitude", "vostro",
            "zenbook", "vivobook", "notebook", "chromebook", "ideapad",
            "legion", "alienware", "precision", "surface laptop", "surface pro",
        ],
    ),
    # Màn hình / Display / Monitor
    (
        ["màn hình", "man hinh", "monitor", "display", "screen"],
        [
            "màn hình", "man hinh", "monitor", "display", "screen",
            "ultrasharp", "viewsonic",
        ],
    ),
    # PC / Desktop / Máy tính để bàn
    (
        ["pc", "máy tính để bàn", "may tinh de ban", "desktop", "workstation"],
        [
            "máy tính để bàn", "may tinh de ban", "desktop", "workstation",
            "optiplex", "imac", "mac mini", "mac studio", "pc để bàn", "pc de ban",
            "pc",
        ],
    ),
    # Bàn phím & Chuột
    (
        ["bàn phím", "chuột", "keyboard", "mouse", "phụ kiện"],
        [
            "bàn phím", "ban phim", "keyboard", "chuột", "chuot", "mouse",
            "trackpad", "keychron", "logitech mx",
        ],
    ),
    # Máy in & Scan & Photocopy
    (
        ["máy in", "may in", "photocopy", "printer", "scanner", "scan"],
        [
            "máy in", "may in", "printer", "photocopy", "scanner", "máy scan",
            "may scan", "laserjet", "inkjet",
        ],
    ),
    # Điện thoại
    (
        ["điện thoại", "dien thoai", "phone", "smartphone", "mobile"],
        [
            "điện thoại", "dien thoai", "smartphone", "iphone", "galaxy s",
            "galaxy z", "galaxy a", "pixel", "redmi", "xiaomi", "phone",
        ],
    ),
    # Thiết bị mạng
    (
        ["thiết bị mạng", "thiet bi mang", "network", "networking"],
        [
            "thiết bị mạng", "thiet bi mang", "router", "switch",
            "access point", "firewall", "cisco", "mikrotik", "unifi",
            "modem", "wifi", "network",
        ],
    ),
    # Máy chủ
    (
        ["máy chủ", "may chu", "server"],
        [
            "máy chủ", "may chu", "server", "poweredge", "proliant", "rackmount",
        ],
    ),
    # Bàn ghế văn phòng
    (
        ["bàn ghế", "ban ghe", "furniture", "nội thất", "noi that"],
        [
            "bàn làm việc", "ban lam viec", "ghế công thái học", "ghe cong thai hoc",
            "ghế xoay", "ghe xoay", "bàn nâng hạ", "ban nang ha", "bàn ghế", "ban ghe",
            "herman miller", "furniture",
        ],
    ),
    # Âm thanh / Tai nghe
    (
        ["tai nghe", "âm thanh", "am thanh", "audio", "headphone", "headset"],
        [
            "tai nghe", "headphone", "headset", "earphones", "airpods",
            "soundbar", "loa", "micro", "microphone",
        ],
    ),
    # Máy chiếu
    (
        ["máy chiếu", "may chieu", "projector"],
        [
            "máy chiếu", "may chieu", "projector", "màn chiếu", "man chieu",
        ],
    ),
    # Lưu trữ / NAS
    (
        ["lưu trữ", "luu tru", "storage", "nas"],
        [
            "synology", "qnap", "nas", "ổ cứng", "o cung", "hdd", "ssd",
        ],
    ),
    # UPS / Bộ lưu điện
    (
        ["ups", "bộ lưu điện", "bo luu dien"],
        [
            "ups", "bộ lưu điện", "bo luu dien", "apc", "santak",
        ],
    ),
]


class AiProxyService:
    _failure_count = 0
    _tripped_until = 0

    @classmethod
    def is_circuit_open(cls) -> bool:
        now = time.time()
        if cls._tripped_until > now:
            return True
        if cls._tripped_until != 0 and cls._tripped_until <= now:
            cls._tripped_until = 0
            cls._failure_count = 0
        return False

    @classmethod
    def record_failure(cls) -> None:
        cls._failure_count += 1
        if cls._failure_count >= 3:
            cls._tripped_until = time.time() + 30

    @classmethod
    def record_success(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def reset_circuit_breaker(cls) -> None:
        cls._failure_count = 0
        cls._tripped_until = 0

    @classmethod
    def check_rate_limit(cls, user_id) -> None:
        now = time.time()
        cache_key = f"ai_rate_limit_{user_id}"
        req_times = cache.get(cache_key, [])
        req_times = [t for t in req_times if now - t < 60]
        if len(req_times) >= 10:
            raise Throttled(detail="Rate limit exceeded. Maximum 10 requests per minute.")
        req_times.append(now)
        cache.set(cache_key, req_times, timeout=60)

    @classmethod
    def reset_rate_limit(cls, user_id=None) -> None:
        if user_id is not None:
            cache.delete(f"ai_rate_limit_{user_id}")
        else:
            cache.clear()

    @staticmethod
    def _find_keyword_pos(kw: str, kw_no_acc: str, text: str, text_no_acc: str) -> int:
        if not kw or not text:
            return -1
        if " " in kw:
            idx1 = text.find(kw)
            idx2 = text_no_acc.find(kw_no_acc)
            if idx1 != -1 and idx2 != -1:
                return min(idx1, idx2)
            return max(idx1, idx2)
        pattern = r'(?:\b|_)' + re.escape(kw) + r'(?:\b|_)'
        m1 = re.search(pattern, text, re.IGNORECASE)
        if m1:
            return m1.start()
        pattern_no_acc = r'(?:\b|_)' + re.escape(kw_no_acc) + r'(?:\b|_)'
        m2 = re.search(pattern_no_acc, text_no_acc, re.IGNORECASE)
        if m2:
            return m2.start()
        return -1

    @classmethod
    def _match_heuristic_category(cls, name: str, description: str = "") -> Optional[Category]:
        db_categories = list(Category.objects.all())
        if not db_categories:
            return None

        name_raw = (name or "").strip().lower()
        name_no_acc = remove_accents(name_raw)
        desc_raw = (description or "").strip().lower()
        desc_no_acc = remove_accents(desc_raw)

        best_match = None
        best_score = (999, 999)

        # 1. Match against FALLBACK_KEYWORD_RULES
        for target_patterns, keywords in FALLBACK_KEYWORD_RULES:
            matched_cat_for_rule = None
            for cat in db_categories:
                c_name = cat.name.strip().lower()
                c_name_no_acc = remove_accents(c_name)
                c_desc = (cat.description or "").strip().lower()
                c_desc_no_acc = remove_accents(c_desc)

                for target in target_patterns:
                    t_clean = target.strip().lower()
                    t_no_acc = remove_accents(t_clean)
                    if (
                        t_clean in c_name
                        or t_no_acc in c_name_no_acc
                        or (c_desc and (t_clean in c_desc or t_no_acc in c_desc_no_acc))
                    ):
                        matched_cat_for_rule = cat
                        break
                if matched_cat_for_rule:
                    break

            if not matched_cat_for_rule:
                continue

            for kw in keywords:
                kw_clean = kw.strip().lower()
                kw_no_acc = remove_accents(kw_clean)

                pos_name = cls._find_keyword_pos(kw_clean, kw_no_acc, name_raw, name_no_acc)
                if pos_name != -1:
                    score = (0, pos_name)
                    if score < best_score:
                        best_score = score
                        best_match = matched_cat_for_rule
                    continue

                pos_desc = cls._find_keyword_pos(kw_clean, kw_no_acc, desc_raw, desc_no_acc)
                if pos_desc != -1:
                    score = (1, pos_desc)
                    if score < best_score:
                        best_score = score
                        best_match = matched_cat_for_rule

        if best_match:
            return best_match

        # 2. Direct DB Category Name matching
        for cat in db_categories:
            c_name = cat.name.strip().lower()
            c_no_acc = remove_accents(c_name)
            if len(c_name) < 2:
                continue
            pos_name = cls._find_keyword_pos(c_name, c_no_acc, name_raw, name_no_acc)
            if pos_name != -1:
                score = (0, pos_name)
                if score < best_score:
                    best_score = score
                    best_match = cat
                continue
            pos_desc = cls._find_keyword_pos(c_name, c_no_acc, desc_raw, desc_no_acc)
            if pos_desc != -1:
                score = (1, pos_desc)
                if score < best_score:
                    best_score = score
                    best_match = cat

        return best_match

    @classmethod
    def _heuristic_fallback(cls, request_id: str, name: str, description: str = "") -> dict:
        matched_cat = cls._match_heuristic_category(name=name, description=description)
        if not matched_cat:
            raise ServiceUnavailable("Không thể tự động phân loại danh mục. Vui lòng chọn thủ công.")

        return {
            "request_id": request_id,
            "suggested_category_id": matched_cat.id,
            "suggested_category_name": matched_cat.name,
            "explanation": f"Gợi ý tự động từ từ khóa phù hợp '{matched_cat.name}' (chế độ dự phòng khi AI không khả dụng).",
            "log_saved": False,
            "is_fallback": True,
        }

    @classmethod
    def _post_request(cls, url: str, payload: dict, headers: dict, timeout: float = 6.0) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw_body = response.read().decode("utf-8")
                return json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ServiceUnavailable("AI worker authentication failed.")
            raise

    @classmethod
    def suggest_category(cls, actor, name: str, description: str = "") -> dict:
        # 1. RBAC Check: Only ADMIN
        if not actor or getattr(actor, "role", None) != User.Role.ADMIN:
            raise PermissionDenied("Only administrators can request AI suggestions.")

        # 2. Input validation
        if not name or not isinstance(name, str) or not name.strip():
            raise ValidationError({"name": ["Tên tài sản không được để trống."]})

        cleaned_name = name.strip()
        if len(cleaned_name) > 200:
            raise ValidationError({"name": ["Tên tài sản không được vượt quá 200 ký tự."]})

        cleaned_description = str(description or "").strip()
        if len(cleaned_description) > 2000:
            raise ValidationError({"description": ["Mô tả không được vượt quá 2000 ký tự."]})

        # 3. Rate limiting (10 req/min per user)
        cls.check_rate_limit(getattr(actor, "id", "anonymous"))

        request_id = str(uuid.uuid4())

        # 4. Circuit breaker check
        if cls.is_circuit_open():
            return cls._heuristic_fallback(request_id=request_id, name=cleaned_name, description=cleaned_description)

        # 5. Fetch active categories
        categories = list(Category.objects.values("id", "name"))
        if not categories:
            raise ServiceUnavailable("Không thể tự động phân loại danh mục. Vui lòng chọn thủ công.")

        # 6. Prepare worker request
        worker_url = settings.AI_WORKER_URL.rstrip("/") + "/api/suggest-category"
        headers = {
            "Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "request_id": request_id,
            "name": cleaned_name,
            "description": cleaned_description,
            "categories": categories,
        }

        # 7. Execute request with error classification and fallback
        trigger_fallback = False
        data = None

        try:
            data = cls._post_request(worker_url, payload, headers, timeout=6.0)
        except ServiceUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ServiceUnavailable("AI worker authentication failed.")

            raw_body = ""
            try:
                raw_body = exc.read().decode("utf-8")
                err_json = json.loads(raw_body)
            except Exception:
                err_json = {}

            err_code = None
            if isinstance(err_json, dict):
                err_code = err_json.get("code")
                if not err_code and isinstance(err_json.get("error"), dict):
                    err_code = err_json["error"].get("code")
                elif not err_code and isinstance(err_json.get("errors"), list) and err_json["errors"]:
                    if isinstance(err_json["errors"][0], dict):
                        err_code = err_json["errors"][0].get("code")

            if exc.code == 429:
                # Quota (3036) or Capacity (3040) or Rate limit from worker
                trigger_fallback = True
            elif exc.code in (500, 502, 503, 504) or exc.code >= 500:
                cls.record_failure()
                trigger_fallback = True
            else:
                cls.record_failure()
                raise ServiceUnavailable(f"AI worker unexpected response ({exc.code}).")

        except (TimeoutError, socket.timeout):
            cls.record_failure()
            trigger_fallback = True
        except urllib.error.URLError:
            cls.record_failure()
            trigger_fallback = True
        except GatewayTimeout:
            cls.record_failure()
            trigger_fallback = True
        except Exception:
            cls.record_failure()
            trigger_fallback = True

        if trigger_fallback:
            return cls._heuristic_fallback(request_id=request_id, name=cleaned_name, description=cleaned_description)

        if not isinstance(data, dict):
            return cls._heuristic_fallback(request_id=request_id, name=cleaned_name, description=cleaned_description)

        suggested_id = data.get("suggested_category_id")
        parsed_id = None
        if suggested_id is not None and not isinstance(suggested_id, bool):
            try:
                val = int(suggested_id)
                if val > 0:
                    parsed_id = val
            except (ValueError, TypeError):
                parsed_id = None
        category = Category.objects.filter(id=parsed_id).first() if parsed_id is not None else None
        if not category:
            return cls._heuristic_fallback(request_id=request_id, name=cleaned_name, description=cleaned_description)

        cls.record_success()
        return {
            "request_id": data.get("request_id", request_id),
            "suggested_category_id": category.id,
            "suggested_category_name": category.name,
            "explanation": str(data.get("explanation", ""))[:500],
            "log_saved": bool(data.get("log_saved", False)),
            "is_fallback": False,
        }

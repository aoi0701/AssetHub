import json
import uuid

def make_test_script(exec_lines):
    return {
        "listen": "test",
        "script": {
            "type": "text/javascript",
            "exec": exec_lines,
        }
    }

def make_prerequest_script(exec_lines):
    return {
        "listen": "prerequest",
        "script": {
            "type": "text/javascript",
            "exec": exec_lines,
        }
    }

def parse_url(url_str):
    if "?" in url_str:
        base_part, query_part = url_str.split("?", 1)
    else:
        base_part, query_part = url_str, ""

    rel_path = base_part
    if rel_path.startswith("{{baseUrl}}"):
        rel_path = rel_path[len("{{baseUrl}}"):]
    if rel_path.startswith("/"):
        rel_path = rel_path[1:]

    path_segments = rel_path.split("/")

    url_obj = {
        "raw": url_str,
        "host": ["{{baseUrl}}"],
        "path": path_segments,
    }

    if query_part:
        query_list = []
        for q in query_part.split("&"):
            if "=" in q:
                k, v = q.split("=", 1)
                query_list.append({"key": k, "value": v})
            else:
                query_list.append({"key": q, "value": ""})
        url_obj["query"] = query_list

    return url_obj

def make_request(name, method, url_str, headers=None, body_str=None, test_lines=None, prerequest_lines=None):
    events = []
    if prerequest_lines:
        events.append(make_prerequest_script(prerequest_lines))
    if test_lines:
        events.append(make_test_script(test_lines))

    header_list = []
    if headers:
        for k, v in headers.items():
            header_list.append({
                "key": k,
                "value": v,
                "type": "text",
            })

    req_data = {
        "name": name,
        "event": events,
        "request": {
            "method": method,
            "header": header_list,
            "url": parse_url(url_str),
        }
    }

    if body_str is not None:
        req_data["request"]["body"] = {
            "mode": "raw",
            "raw": body_str,
            "options": {"raw": {"language": "json"}}
        }

    return req_data


def build_collection():
    items = []

    # -------------------------------------------------------------
    # 00_HealthCheck
    # -------------------------------------------------------------
    health_items = [
        make_request(
            name="Health Check",
            method="GET",
            url_str="{{baseUrl}}/api/health/",
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Service and DB report OK status', function () {",
                "    var jsonData = pm.response.json();",
                "    pm.expect(jsonData.status).to.eql('ok');",
                "    pm.expect(jsonData.db).to.eql('ok');",
                "});",
            ]
        )
    ]
    items.append({"name": "00_HealthCheck", "item": health_items})

    # -------------------------------------------------------------
    # 01_Auth
    # -------------------------------------------------------------
    auth_items = [
        # 1. Admin login
        make_request(
            name="Admin Login",
            method="POST",
            url_str="{{baseUrl}}/api/auth/login/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "username": "{{adminUsername}}",\n  "password": "{{adminPassword}}"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns access and refresh tokens for ADMIN', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.expect(data).to.have.property('refresh');",
                "    pm.expect(data.user.role).to.eql('ADMIN');",
                "    pm.environment.set('adminToken', data.access);",
                "    pm.environment.set('adminRefreshToken', data.refresh);",
                "});",
            ]
        ),
        # 2. Manager login
        make_request(
            name="Manager Login",
            method="POST",
            url_str="{{baseUrl}}/api/auth/login/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "username": "{{managerUsername}}",\n  "password": "{{managerPassword}}"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns tokens for MANAGER', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.expect(data.user.role).to.eql('MANAGER');",
                "    pm.environment.set('managerToken', data.access);",
                "    pm.environment.set('managerDeptId', String(data.user.department_id));",
                "});",
            ]
        ),
        # 3. User login
        make_request(
            name="User Login",
            method="POST",
            url_str="{{baseUrl}}/api/auth/login/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "username": "{{userUsername}}",\n  "password": "{{userPassword}}"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns tokens for USER', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.expect(data.user.role).to.eql('USER');",
                "    pm.environment.set('userToken', data.access);",
                "    pm.environment.set('userRefreshToken', data.refresh);",
                "    pm.environment.set('userId', String(data.user.id));",
                "    pm.environment.set('userDeptId', String(data.user.department_id));",
                "});",
            ]
        ),
        # 4. User Registration
        make_request(
            name="User Registration",
            method="POST",
            url_str="{{baseUrl}}/api/auth/register/",
            headers={"Content-Type": "application/json"},
            prerequest_lines=[
                "var ts = Date.now();",
                "pm.environment.set('regUsername', 'testuser_' + ts);",
                "pm.environment.set('regEmail', 'testuser_' + ts + '@assethub.local');",
            ],
            body_str='{\n  "username": "{{regUsername}}",\n  "email": "{{regEmail}}",\n  "password": "RegisterPass123!",\n  "full_name": "Test Register User",\n  "phone": "0987654321"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('User registration message returned', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('message');",
                "});",
            ]
        ),
        # 5. Admin Activates User
        make_request(
            name="Admin Activates User",
            method="PATCH",
            url_str="{{baseUrl}}/api/users/{{regUserId}}/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            prerequest_lines=[
                "pm.sendRequest({",
                "    url: pm.environment.get('baseUrl') + '/api/users/?search=' + pm.environment.get('regUsername'),",
                "    method: 'GET',",
                "    header: {",
                "        'Authorization': 'Bearer ' + pm.environment.get('adminToken')",
                "    }",
                "}, function (err, res) {",
                "    if (!err && res.code === 200) {",
                "        var users = res.json();",
                "        var list = users.results ? users.results : users;",
                "        if (list && list.length > 0) {",
                "            pm.environment.set('regUserId', String(list[0].id));",
                "        }",
                "    }",
                "});",
            ],
            body_str='{\n  "is_active": true,\n  "department_id": {{userDeptId}}\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('User is now active', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.is_active).to.be.true;",
                "});",
            ]
        ),
        # 6. Activated User Login
        make_request(
            name="Activated User Login",
            method="POST",
            url_str="{{baseUrl}}/api/auth/login/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "username": "{{regUsername}}",\n  "password": "RegisterPass123!"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns tokens for newly activated user', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.environment.set('regUserToken', data.access);",
                "    pm.environment.set('regUserRefreshToken', data.refresh);",
                "});",
            ]
        ),
        # 7. Refresh Token
        make_request(
            name="Refresh Token",
            method="POST",
            url_str="{{baseUrl}}/api/auth/refresh/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "refresh": "{{regUserRefreshToken}}"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns new access token', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.environment.set('regUserToken', data.access);",
                "    if (data.refresh) {",
                "        pm.environment.set('regUserRefreshToken', data.refresh);",
                "    }",
                "});",
            ]
        ),
        # 8. Profile Get
        make_request(
            name="Get Profile",
            method="GET",
            url_str="{{baseUrl}}/api/auth/profile/",
            headers={"Authorization": "Bearer {{regUserToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns profile of current user', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.username).to.eql(pm.environment.get('regUsername'));",
                "});",
            ]
        ),
        # 9. Profile Patch
        make_request(
            name="Patch Profile",
            method="PATCH",
            url_str="{{baseUrl}}/api/auth/profile/",
            headers={
                "Authorization": "Bearer {{regUserToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "full_name": "Updated Profile Name",\n  "phone": "0911223344"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Profile updated successfully', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.full_name).to.eql('Updated Profile Name');",
                "    pm.expect(data.phone).to.eql('0911223344');",
                "});",
            ]
        ),
        # 10. Change Password
        make_request(
            name="Change Password",
            method="POST",
            url_str="{{baseUrl}}/api/auth/change-password/",
            headers={
                "Authorization": "Bearer {{regUserToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "old_password": "RegisterPass123!",\n  "new_password": "BrandNewPassword123!"\n}',
            test_lines=[
                "pm.test('Status code is 200 or 204', function () {",
                "    pm.expect(pm.response.code).to.be.oneOf([200, 204]);",
                "});",
            ]
        ),
        # 11. Login With New Password
        make_request(
            name="Login With New Password",
            method="POST",
            url_str="{{baseUrl}}/api/auth/login/",
            headers={"Content-Type": "application/json"},
            body_str='{\n  "username": "{{regUsername}}",\n  "password": "BrandNewPassword123!"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Successfully authenticated with new password', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('access');",
                "    pm.environment.set('regUserToken', data.access);",
                "    pm.environment.set('regUserRefreshToken', data.refresh);",
                "});",
            ]
        ),
        # 12. Logout
        make_request(
            name="Logout",
            method="POST",
            url_str="{{baseUrl}}/api/auth/logout/",
            headers={
                "Authorization": "Bearer {{regUserToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "refresh": "{{regUserRefreshToken}}"\n}',
            test_lines=[
                "pm.test('Status code is 200 or 204', function () {",
                "    pm.expect(pm.response.code).to.be.oneOf([200, 204]);",
                "});",
            ]
        ),
    ]
    items.append({"name": "01_Auth", "item": auth_items})

    # -------------------------------------------------------------
    # 02_Departments_and_Categories
    # -------------------------------------------------------------
    dept_cat_items = [
        # 1. List departments
        make_request(
            name="List Departments",
            method="GET",
            url_str="{{baseUrl}}/api/departments/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Departments list returned', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    pm.expect(list.length).to.be.above(0);",
                "});",
            ]
        ),
        # 2. Create department
        make_request(
            name="Create Department",
            method="POST",
            url_str="{{baseUrl}}/api/departments/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            prerequest_lines=[
                "var ts = Date.now();",
                "pm.environment.set('newDeptCode', 'DP' + (ts % 1000000));",
                "pm.environment.set('newDeptName', 'Dept ' + ts);",
            ],
            body_str='{\n  "code": "{{newDeptCode}}",\n  "name": "{{newDeptName}}",\n  "description": "Automated Department Test"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Department created with code', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.code).to.eql(pm.environment.get('newDeptCode'));",
                "    pm.environment.set('createdDeptId', String(data.id));",
                "});",
            ]
        ),
        # 3. Update department
        make_request(
            name="Update Department",
            method="PATCH",
            url_str="{{baseUrl}}/api/departments/{{createdDeptId}}/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "description": "Updated Department Description"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Description updated', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.description).to.eql('Updated Department Description');",
                "});",
            ]
        ),
        # 4. List categories
        make_request(
            name="List Categories",
            method="GET",
            url_str="{{baseUrl}}/api/categories/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Categories list returned', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    pm.expect(list.length).to.be.above(0);",
                "});",
            ]
        ),
        # 5. Create category
        make_request(
            name="Create Category",
            method="POST",
            url_str="{{baseUrl}}/api/categories/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            prerequest_lines=[
                "var ts = Date.now();",
                "pm.environment.set('newCatName', 'Cat ' + ts);",
            ],
            body_str='{\n  "name": "{{newCatName}}",\n  "description": "Automated Category Test"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Category created with name', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.name).to.eql(pm.environment.get('newCatName'));",
                "    pm.environment.set('createdCatId', String(data.id));",
                "});",
            ]
        ),
        # 6. Update category
        make_request(
            name="Update Category",
            method="PATCH",
            url_str="{{baseUrl}}/api/categories/{{createdCatId}}/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "description": "Updated Category Description"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Category description updated', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.description).to.eql('Updated Category Description');",
                "});",
            ]
        ),
    ]
    items.append({"name": "02_Departments_and_Categories", "item": dept_cat_items})

    # -------------------------------------------------------------
    # 03_User_Admin
    # -------------------------------------------------------------
    user_admin_items = [
        # 1. List users
        make_request(
            name="List Users",
            method="GET",
            url_str="{{baseUrl}}/api/users/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Users list returned', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    pm.expect(list.length).to.be.above(0);",
                "});",
            ]
        ),
        # 2. Detail user
        make_request(
            name="Detail User",
            method="GET",
            url_str="{{baseUrl}}/api/users/{{regUserId}}/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('User detail matches regUserId', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.id).to.eql(Number(pm.environment.get('regUserId')));",
                "});",
            ]
        ),
        # 3. Update role and department
        make_request(
            name="Update User Role and Department",
            method="PATCH",
            url_str="{{baseUrl}}/api/users/{{regUserId}}/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "role": "USER",\n  "department_id": {{createdDeptId}}\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('User department updated', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.department_id).to.eql(Number(pm.environment.get('createdDeptId')));",
                "});",
            ]
        ),
        # 4. Block deactivation if user holds assets
        make_request(
            name="Block Deactivation If User Holds Assets",
            method="PATCH",
            url_str="{{baseUrl}}/api/users/{{userId}}/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "is_active": false\n}',
            test_lines=[
                "pm.test('Status code is 409 Conflict', function () {",
                "    pm.response.to.have.status(409);",
                "});",
            ]
        ),
    ]
    items.append({"name": "03_User_Admin", "item": user_admin_items})

    # -------------------------------------------------------------
    # 04_Assets_Lifecycle
    # -------------------------------------------------------------
    asset_lifecycle_items = [
        # 1. Create asset
        make_request(
            name="Create Asset",
            method="POST",
            url_str="{{baseUrl}}/api/assets/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            prerequest_lines=[
                "var ts = Date.now();",
                "pm.environment.set('assetSerial', 'SN-LC-' + ts);",
                "pm.environment.set('assetName', 'Asset Lifecycle ' + ts);",
            ],
            body_str='{\n  "name": "{{assetName}}",\n  "category_id": {{createdCatId}},\n  "department_id": {{createdDeptId}},\n  "serial_number": "{{assetSerial}}",\n  "reason": "Purchased for full lifecycle testing"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Asset created as AVAILABLE in createdDept', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('AVAILABLE');",
                "    pm.expect(data.department_id).to.eql(Number(pm.environment.get('createdDeptId')));",
                "    pm.environment.set('lifecycleAssetId', String(data.id));",
                "    pm.environment.set('lifecycleAssetCode', data.asset_code);",
                "});",
            ]
        ),
        # 2. List assets with filters
        make_request(
            name="List Assets With Filters",
            method="GET",
            url_str="{{baseUrl}}/api/assets/?category_id={{createdCatId}}&status=AVAILABLE",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Filtered asset list contains created asset', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    var found = list.some(function(a) { return a.id === Number(pm.environment.get('lifecycleAssetId')); });",
                "    pm.expect(found).to.be.true;",
                "});",
            ]
        ),
        # 3. Allocate asset to regUserId (who is in createdDeptId)
        make_request(
            name="Allocate Asset",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/allocate/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "assigned_to_user_id": {{regUserId}},\n  "reason": "Allocating to registered user"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Asset status transitions to IN_USE', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('IN_USE');",
                "    pm.expect(data.assigned_to_user_id).to.eql(Number(pm.environment.get('regUserId')));",
                "});",
            ]
        ),
        # 4. Revoke asset
        make_request(
            name="Revoke Asset",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/revoke/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "received_confirmed": true,\n  "condition": "GOOD",\n  "reason": "Revoking back to stock"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Asset transitions back to AVAILABLE', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('AVAILABLE');",
                "    pm.expect(data.assigned_to_user_id).to.be.null;",
                "});",
            ]
        ),
        # 5. Transfer asset to department 1 (IT)
        make_request(
            name="Transfer Asset",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/transfer/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "to_department_id": 1,\n  "reason": "Transferring asset to IT department"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Asset transferred to IT department', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('AVAILABLE');",
                "    pm.expect(data.department_id).to.eql(1);",
                "});",
            ]
        ),
        # 6. Mark damaged
        make_request(
            name="Mark Damaged",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/mark-damaged/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "reason": "Audit showed broken component"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Status transitions to DAMAGED_PENDING_REPAIR', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('DAMAGED_PENDING_REPAIR');",
                "});",
            ]
        ),
        # 7. Send maintenance
        make_request(
            name="Send Maintenance",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/send-maintenance/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "reason": "Sent to vendor repair service"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Status transitions to UNDER_MAINTENANCE', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('UNDER_MAINTENANCE');",
                "});",
            ]
        ),
        # 8. Complete maintenance
        make_request(
            name="Complete Maintenance",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/complete-maintenance/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "result": "RETURN_TO_STOCK",\n  "reason": "Repairs completed, restored to good order"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Status transitions to AVAILABLE', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('AVAILABLE');",
                "});",
            ]
        ),
        # 9. Propose disposal
        make_request(
            name="Propose Disposal",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/propose-disposal/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "reason": "Old hardware, obsolete tech"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Status transitions to PENDING_DISPOSAL', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('PENDING_DISPOSAL');",
                "});",
            ]
        ),
        # 10. Confirm disposal
        make_request(
            name="Confirm Disposal",
            method="POST",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/confirm-disposal/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "reason": "Disposal committee approved write-off"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Status transitions to DISPOSED', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('DISPOSED');",
                "});",
            ]
        ),
    ]
    items.append({"name": "04_Assets_Lifecycle", "item": asset_lifecycle_items})

    # -------------------------------------------------------------
    # 05_Requests
    # -------------------------------------------------------------
    requests_items = [
        # 1. Admin creates fresh asset in IT department for requests flow
        make_request(
            name="Admin Creates Dedicated Request Asset",
            method="POST",
            url_str="{{baseUrl}}/api/assets/",
            headers={
                "Authorization": "Bearer {{adminToken}}",
                "Content-Type": "application/json",
            },
            prerequest_lines=[
                "var ts = Date.now();",
                "pm.environment.set('reqAssetSerial', 'REQ-SN-' + ts);",
                "pm.environment.set('reqAssetName', 'Request Laptop ' + ts);",
            ],
            body_str='{\n  "name": "{{reqAssetName}}",\n  "category_id": 1,\n  "department_id": 1,\n  "serial_number": "{{reqAssetSerial}}",\n  "reason": "Dedicated asset for request test cycle"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Asset created for requests', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('AVAILABLE');",
                "    pm.environment.set('reqAssetId', String(data.id));",
                "});",
            ]
        ),
        # 2. User creates ALLOCATE request
        make_request(
            name="User Creates Allocate Request",
            method="POST",
            url_str="{{baseUrl}}/api/requests/",
            headers={
                "Authorization": "Bearer {{userToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "request_type": "ALLOCATE",\n  "requested_category_id": 1,\n  "reason": "Requesting laptop for project development"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Request is in PENDING state', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('PENDING');",
                "    pm.expect(data.request_type).to.eql('ALLOCATE');",
                "    pm.environment.set('userAllocRequestId', String(data.id));",
                "});",
            ]
        ),
        # 3. Manager approves ALLOCATE request
        make_request(
            name="Manager Approves Allocate Request",
            method="POST",
            url_str="{{baseUrl}}/api/requests/{{userAllocRequestId}}/approve/",
            headers={
                "Authorization": "Bearer {{managerToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "selected_asset_id": {{reqAssetId}}\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Request status is APPROVED', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('APPROVED');",
                "    pm.expect(data.asset_id).to.eql(Number(pm.environment.get('reqAssetId')));",
                "});",
            ]
        ),
        # 4. User creates RETURN request
        make_request(
            name="User Creates Return Request",
            method="POST",
            url_str="{{baseUrl}}/api/requests/",
            headers={
                "Authorization": "Bearer {{userToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "request_type": "RETURN",\n  "asset_id": {{reqAssetId}},\n  "reason": "Returning laptop after project phase 1"\n}',
            test_lines=[
                "pm.test('Status code is 201 Created', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "pm.test('Return request created as PENDING', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('PENDING');",
                "    pm.environment.set('userReturnRequestId', String(data.id));",
                "});",
            ]
        ),
        # 5. Duplicate prevention: User creates second RETURN request for same asset while first pending -> 409
        make_request(
            name="Duplicate Return Request Prevention",
            method="POST",
            url_str="{{baseUrl}}/api/requests/",
            headers={
                "Authorization": "Bearer {{userToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "request_type": "RETURN",\n  "asset_id": {{reqAssetId}},\n  "reason": "Duplicate return request"\n}',
            test_lines=[
                "pm.test('Status code is 409 Conflict', function () {",
                "    pm.response.to.have.status(409);",
                "});",
            ]
        ),
        # 6. Manager rejects RETURN request
        make_request(
            name="Manager Rejects Return Request",
            method="POST",
            url_str="{{baseUrl}}/api/requests/{{userReturnRequestId}}/reject/",
            headers={
                "Authorization": "Bearer {{managerToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "rejection_reason": "Device still needed for phase 2 QA testing"\n}',
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Request status is REJECTED', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data.status).to.eql('REJECTED');",
                "    pm.expect(data.rejection_reason).to.eql('Device still needed for phase 2 QA testing');",
                "});",
            ]
        ),
    ]
    items.append({"name": "05_Requests", "item": requests_items})

    # -------------------------------------------------------------
    # 06_History_and_Reports
    # -------------------------------------------------------------
    history_report_items = [
        # 1. Admin asset history
        make_request(
            name="Admin Asset History",
            method="GET",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/history/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('History logs returned for asset', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    pm.expect(list.length).to.be.above(0);",
                "});",
            ]
        ),
        # 2. User personal history
        make_request(
            name="User Personal Asset History",
            method="GET",
            url_str="{{baseUrl}}/api/me/asset-history/",
            headers={"Authorization": "Bearer {{userToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns personal history array', function () {",
                "    var data = pm.response.json();",
                "    var list = data.results ? data.results : data;",
                "    pm.expect(Array.isArray(list)).to.be.true;",
                "});",
            ]
        ),
        # 3. Reports KPIs
        make_request(
            name="Reports KPIs",
            method="GET",
            url_str="{{baseUrl}}/api/reports/kpis/",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns KPI metrics', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('total_active');",
                "    pm.expect(data).to.have.property('available');",
                "    pm.expect(data).to.have.property('in_use');",
                "});",
            ]
        ),
        # 4. Reports Charts With Filters
        make_request(
            name="Reports Charts With Filters",
            method="GET",
            url_str="{{baseUrl}}/api/reports/charts/?department_id=1",
            headers={"Authorization": "Bearer {{adminToken}}"},
            test_lines=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "pm.test('Returns chart distributions', function () {",
                "    var data = pm.response.json();",
                "    pm.expect(data).to.have.property('status_distribution');",
                "});",
            ]
        ),
    ]
    items.append({"name": "06_History_and_Reports", "item": history_report_items})

    # -------------------------------------------------------------
    # 07_Security_and_Negative_Tests
    # -------------------------------------------------------------
    security_items = [
        # 1. User 403 on asset history
        make_request(
            name="User Forbidden On Asset History",
            method="GET",
            url_str="{{baseUrl}}/api/assets/{{lifecycleAssetId}}/history/",
            headers={"Authorization": "Bearer {{userToken}}"},
            test_lines=[
                "pm.test('Status code is 403 Forbidden', function () {",
                "    pm.response.to.have.status(403);",
                "});",
            ]
        ),
        # 2. User 403 on reports
        make_request(
            name="User Forbidden On Reports KPIs",
            method="GET",
            url_str="{{baseUrl}}/api/reports/kpis/",
            headers={"Authorization": "Bearer {{userToken}}"},
            test_lines=[
                "pm.test('Status code is 403 Forbidden', function () {",
                "    pm.response.to.have.status(403);",
                "});",
            ]
        ),
        # 3. Manager 403 out of scope
        make_request(
            name="Manager Forbidden On Unallocated Department Reports",
            method="GET",
            url_str="{{baseUrl}}/api/reports/kpis/?department_id=unallocated",
            headers={"Authorization": "Bearer {{managerToken}}"},
            test_lines=[
                "pm.test('Status code is 403 Forbidden', function () {",
                "    pm.response.to.have.status(403);",
                "});",
            ]
        ),
        # 4. 409 Conflict on double approve
        make_request(
            name="Conflict On Double Approve",
            method="POST",
            url_str="{{baseUrl}}/api/requests/{{userAllocRequestId}}/approve/",
            headers={
                "Authorization": "Bearer {{managerToken}}",
                "Content-Type": "application/json",
            },
            body_str='{\n  "selected_asset_id": {{reqAssetId}}\n}',
            test_lines=[
                "pm.test('Status code is 409 Conflict', function () {",
                "    pm.response.to.have.status(409);",
                "});",
            ]
        ),
    ]
    items.append({"name": "07_Security_and_Negative_Tests", "item": security_items})

    collection = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": "AssetHub API End-to-End Collection",
            "description": "Comprehensive Postman test collection for AssetHub REST API covering all modules and roles.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": items,
    }
    return collection


def build_environment(is_template=False):
    admin_pw = "" if is_template else "DemoPassword123!"
    mgr_pw = "" if is_template else "DemoPassword123!"
    usr_pw = "" if is_template else "DemoPassword123!"
    return {
        "id": str(uuid.uuid4()),
        "name": "AssetHub Environment Template" if is_template else "AssetHub Environment",
        "values": [
            {"key": "baseUrl", "value": "http://127.0.0.1:8000", "type": "default", "enabled": True},
            {"key": "adminUsername", "value": "admin", "type": "default", "enabled": True},
            {"key": "adminPassword", "value": admin_pw, "type": "secret", "enabled": True},
            {"key": "managerUsername", "value": "manager_it", "type": "default", "enabled": True},
            {"key": "managerPassword", "value": mgr_pw, "type": "secret", "enabled": True},
            {"key": "userUsername", "value": "user_it_1", "type": "default", "enabled": True},
            {"key": "userPassword", "value": usr_pw, "type": "secret", "enabled": True},
            {"key": "adminToken", "value": "", "type": "default", "enabled": True},
            {"key": "adminRefreshToken", "value": "", "type": "default", "enabled": True},
            {"key": "managerToken", "value": "", "type": "default", "enabled": True},
            {"key": "managerDeptId", "value": "1", "type": "default", "enabled": True},
            {"key": "userToken", "value": "", "type": "default", "enabled": True},
            {"key": "userRefreshToken", "value": "", "type": "default", "enabled": True},
            {"key": "userId", "value": "4", "type": "default", "enabled": True},
            {"key": "userDeptId", "value": "1", "type": "default", "enabled": True},
            {"key": "regUsername", "value": "", "type": "default", "enabled": True},
            {"key": "regEmail", "value": "", "type": "default", "enabled": True},
            {"key": "regUserId", "value": "", "type": "default", "enabled": True},
            {"key": "regUserToken", "value": "", "type": "default", "enabled": True},
            {"key": "regUserRefreshToken", "value": "", "type": "default", "enabled": True},
            {"key": "newDeptCode", "value": "", "type": "default", "enabled": True},
            {"key": "newDeptName", "value": "", "type": "default", "enabled": True},
            {"key": "createdDeptId", "value": "", "type": "default", "enabled": True},
            {"key": "newCatName", "value": "", "type": "default", "enabled": True},
            {"key": "createdCatId", "value": "", "type": "default", "enabled": True},
            {"key": "assetSerial", "value": "", "type": "default", "enabled": True},
            {"key": "assetName", "value": "", "type": "default", "enabled": True},
            {"key": "lifecycleAssetId", "value": "", "type": "default", "enabled": True},
            {"key": "lifecycleAssetCode", "value": "", "type": "default", "enabled": True},
            {"key": "reqAssetSerial", "value": "", "type": "default", "enabled": True},
            {"key": "reqAssetName", "value": "", "type": "default", "enabled": True},
            {"key": "reqAssetId", "value": "", "type": "default", "enabled": True},
            {"key": "userAllocRequestId", "value": "", "type": "default", "enabled": True},
            {"key": "userReturnRequestId", "value": "", "type": "default", "enabled": True},
        ],
        "_postman_variable_scope": "environment",
        "_postman_exported_at": "2026-10-29T12:00:00.000Z",
        "_postman_exported_using": "Postman/10.0.0"
    }

if __name__ == "__main__":
    coll = build_collection()
    with open("backend/docs/postman/AssetHub.postman_collection.json", "w", encoding="utf-8") as f:
        json.dump(coll, f, indent=2, ensure_ascii=False)
    print("Wrote backend/docs/postman/AssetHub.postman_collection.json")

    template_env = build_environment(is_template=True)
    with open("backend/docs/postman/AssetHub.postman_environment.json", "w", encoding="utf-8") as f:
        json.dump(template_env, f, indent=2, ensure_ascii=False)
    print("Wrote backend/docs/postman/AssetHub.postman_environment.json (template with empty secrets)")

    local_env = build_environment(is_template=False)
    with open("tools/postman/environment.local.json", "w", encoding="utf-8") as f:
        json.dump(local_env, f, indent=2, ensure_ascii=False)
    print("Wrote tools/postman/environment.local.json (local credentials)")

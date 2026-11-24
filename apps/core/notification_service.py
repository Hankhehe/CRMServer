import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional
from django.core.mail import EmailMultiAlternatives, get_connection
from django.urls import reverse
from django.utils.html import escape
from .models import SystemSetting, UserProfile, AuditLog

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _get_smtp_config():
    sys_settings = SystemSetting.get_settings()
    host = (sys_settings.smtp_host or "").strip()
    port = sys_settings.smtp_port or 587
    use_tls = bool(sys_settings.smtp_use_tls)
    use_ssl = bool(sys_settings.smtp_use_ssl)
    username = (sys_settings.smtp_user or "").strip()
    password = sys_settings.smtp_password or ""
    from_email = (sys_settings.smtp_from_email or username or "noreply@crm.local").strip()
    site_base_url = (sys_settings.site_base_url or "http://127.0.0.1:8000").strip().rstrip("/")
    return {
        "host": host,
        "port": port,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "username": username,
        "password": password,
        "from_email": from_email,
        "site_base_url": site_base_url,
    }


def send_test_smtp_email(target_email: str) -> tuple[bool, str]:
    """同步寄送測試信，供系統設定頁面即時檢視結果"""
    cfg = _get_smtp_config()
    if not cfg["host"]:
        return False, "尚未設定 SMTP 伺服器主機 (SMTP Host)，請先填寫並儲存設定。"
    if not target_email:
        return False, "請輸入收件測試信箱。"

    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S (Asia/Taipei)")
    subject = "[CRM Server] SMTP 郵件伺服器連線測試信"
    text_body = (
        f"您好，\n\n"
        f"這是一封來自 CRM & 資產管理系統的測試郵件。\n"
        f"若您收到此信件，代表系統的 SMTP 郵件通知設定已正常運作！\n\n"
        f"發送時間：{now_str}\n"
        f"SMTP 主機：{cfg['host']}:{cfg['port']}\n"
        f"系統網址：{cfg['site_base_url']}\n"
    )
    html_body = f"""
    <div style="font-family: 'Inter', 'Microsoft JhengHei', sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden;">
      <div style="background: #4f46e5; color: #ffffff; padding: 16px 24px; font-size: 18px; font-weight: 700;">
        CRM Server - SMTP 郵件連線測試成功
      </div>
      <div style="padding: 24px; color: #0f172a; line-height: 1.6; background: #ffffff;">
        <p>您好，</p>
        <p>這是一封來自 <strong>CRM & 資產管理系統</strong> 的自動測試郵件。若您收到此信件，代表系統的關注客戶通知郵件功能已設定完成並可正常發信！</p>
        <div style="background: #f8fafc; border-left: 4px solid #4f46e5; padding: 12px 16px; margin: 16px 0; font-size: 14px;">
          <div><strong>發送時間：</strong> {escape(now_str)}</div>
          <div><strong>SMTP 主機：</strong> {escape(cfg['host'])}:{cfg['port']}</div>
          <div><strong>系統網址：</strong> <a href="{escape(cfg['site_base_url'])}" style="color: #4f46e5;">{escape(cfg['site_base_url'])}</a></div>
        </div>
      </div>
    </div>
    """

    try:
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["username"] or None,
            password=cfg["password"] or None,
            use_tls=cfg["use_tls"],
            use_ssl=cfg["use_ssl"],
            timeout=10,
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=cfg["from_email"],
            to=[target_email],
            connection=connection,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True, f"測試信已成功寄出至 {target_email}！"
    except Exception as e:
        return False, f"寄送測試信失敗：{str(e)}"


def _send_emails_worker(subject: str, text_body: str, html_body: str, recipient_list: list[str], customer_code: str, event_title: str):
    cfg = _get_smtp_config()
    if not cfg["host"] or not recipient_list:
        return

    try:
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["username"] or None,
            password=cfg["password"] or None,
            use_tls=cfg["use_tls"],
            use_ssl=cfg["use_ssl"],
            timeout=12,
        )
        messages_to_send = []
        for email_addr in recipient_list:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=cfg["from_email"],
                to=[email_addr],
                connection=connection,
            )
            msg.attach_alternative(html_body, "text/html")
            messages_to_send.append(msg)

        connection.send_messages(messages_to_send)
    except Exception as e:
        try:
            AuditLog.objects.create(
                user=None,
                action="Email通知寄送失敗",
                target_type="Customer",
                target_identifier=customer_code,
                detail=f"事件: {event_title} | 收件人: {', '.join(recipient_list)} | 錯誤: {str(e)}",
            )
        except Exception:
            pass


def notify_customer_followers(
    customer,
    actor,
    event_category: str,  # "comment" or "data_change"
    event_title: str,
    event_detail: str = "",
    anchor: str = "",
    sync: bool = False,
) -> list[str]:
    """
    當客戶發生「新留言/執行軌跡 (comment)」或「資料/合約/授權/設備變更 (data_change)」時，
    找出所有關注該客戶且開啟對應通知開關的使用者（排除操作者本人），發送 Email 通知。
    回傳實際排入寄送的收件信箱列表。
    """
    if not customer:
        return []

    from apps.customers.models import CustomerFollow

    follows = (
        CustomerFollow.objects.filter(customer=customer)
        .select_related("user", "user__profile")
        .all()
    )

    recipient_emails = []
    for f in follows:
        u = f.user
        if not u or not u.is_active:
            continue
        # 排除操作者本人
        if actor and getattr(actor, "is_authenticated", False) and u.pk == actor.pk:
            continue

        email_addr = (u.email or "").strip()
        if not email_addr:
            continue

        profile, _ = UserProfile.objects.get_or_create(user=u)
        if not profile.receive_email_notifications:
            continue
        if event_category == "comment" and not profile.notify_on_new_comment:
            continue
        if event_category == "data_change" and not profile.notify_on_data_change:
            continue

        if email_addr not in recipient_emails:
            recipient_emails.append(email_addr)

    if not recipient_emails:
        return []

    cfg = _get_smtp_config()
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    actor_name = "系統"
    if actor and getattr(actor, "is_authenticated", False):
        full_name = actor.get_full_name()
        actor_name = f"{full_name} ({actor.username})" if full_name else actor.username

    detail_path = reverse("customer-detail", kwargs={"pk": customer.pk})
    customer_url = f"{cfg['site_base_url']}{detail_path}{anchor}"
    account_center_url = f"{cfg['site_base_url']}{reverse('account-center')}"

    cat_label = "新留言 / 執行軌跡" if event_category == "comment" else "客戶資料 / 資產異動"
    subject = f"[CRM 關注通知] {customer.name} ({customer.code}) - {event_title}"

    text_body = (
        f"您好，\n\n"
        f"您所關注的客戶「{customer.name} ({customer.code})」有新的動態更新：\n\n"
        f"【通知類型】{cat_label}\n"
        f"【事件項目】{event_title}\n"
        f"【操作人員】{actor_name}\n"
        f"【發生時間】{now_str} (Asia/Taipei)\n"
        f"【詳細內容】\n{event_detail or '（無額外說明）'}\n\n"
        f"👉 點此檢視客戶詳情：{customer_url}\n"
        f"⚙️ 若要調整通知偏好或取消關注，請至個人帳號中心：{account_center_url}\n"
    )

    badge_bg = "#eef2ff" if event_category == "comment" else "#ecfdf5"
    badge_color = "#4f46e5" if event_category == "comment" else "#059669"

    html_body = f"""
    <div style="font-family: 'Inter', 'Microsoft JhengHei', sans-serif; max-width: 640px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background: #ffffff;">
      <div style="background: #4f46e5; color: #ffffff; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between;">
        <div style="font-size: 17px; font-weight: 700;">CRM 客戶關注動態通知</div>
      </div>
      <div style="padding: 24px; color: #0f172a; line-height: 1.65;">
        <div style="margin-bottom: 16px;">
          <span style="display: inline-block; background: {badge_bg}; color: {badge_color}; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 9999px;">
            {escape(cat_label)}
          </span>
        </div>
        <h2 style="font-size: 18px; margin: 0 0 12px 0; color: #0f172a;">
          {escape(customer.name)} <span style="font-family: monospace; color: #4f46e5; font-size: 15px;">({escape(customer.code)})</span>
        </h2>
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 20px; font-size: 14px;">
          <div style="margin-bottom: 6px;"><strong>異動事件：</strong> {escape(event_title)}</div>
          <div style="margin-bottom: 6px;"><strong>操作同仁：</strong> {escape(actor_name)}</div>
          <div style="margin-bottom: 10px;"><strong>發生時間：</strong> {escape(now_str)}</div>
          <div style="border-top: 1px dashed #cbd5e1; padding-top: 10px; white-space: pre-wrap; color: #334155;">{escape(event_detail or '（無額外說明）')}</div>
        </div>
        <div style="margin-top: 20px;">
          <a href="{escape(customer_url)}" style="display: inline-block; background: #4f46e5; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 14px;">
            前往查看客戶詳情
          </a>
        </div>
        <div style="margin-top: 28px; padding-top: 14px; border-top: 1px solid #f1f5f9; font-size: 12px; color: #64748b;">
          這封信是因為您關注了客戶「{escape(customer.name)}」且開啟了 Email 通知功能。<br>
          若不想再收到通知，請前往 <a href="{escape(account_center_url)}" style="color: #4f46e5;">個人帳號中心</a> 調整收信偏好或取消關注。
        </div>
      </div>
    </div>
    """

    if cfg["host"]:
        if sync:
            _send_emails_worker(subject, text_body, html_body, recipient_emails, customer.code, event_title)
        else:
            t = threading.Thread(
                target=_send_emails_worker,
                args=(subject, text_body, html_body, recipient_emails, customer.code, event_title),
                daemon=True,
            )
            t.start()

    return recipient_emails


def notify_approval_submitted(
    module_type: str,  # "license" | "warranty" | "hardware"
    customer,
    actor,
    target_identifier: str,
    event_detail: str,
    review_path: str,
    sync: bool = False,
) -> list[str]:
    """
    當無覆核權限之開單人送出授權書、維護合約或硬體出庫申請進入「待覆核」時，
    自動寄送 Email 給所有具備該模組覆核權限（且可存取該客戶、開啟 Email 通知總開關）之主管。
    """
    from django.contrib.auth import get_user_model
    from .permissions import (
        user_can_approve_licenses,
        user_can_approve_contracts,
        user_can_approve_hardware,
        user_can_access_customer,
    )

    User = get_user_model()
    module_label_map = {
        "license": "軟體授權書",
        "warranty": "維護保固合約",
        "hardware": "硬體設備出庫",
    }
    mod_label = module_label_map.get(module_type, "單據")

    active_users = User.objects.filter(is_active=True).select_related("profile", "profile__role_obj")
    recipient_emails = []

    for u in active_users:
        if actor and getattr(actor, "is_authenticated", False) and u.pk == actor.pk:
            continue
        email_addr = (u.email or "").strip()
        if not email_addr:
            continue

        has_perm = False
        if module_type == "license":
            has_perm = user_can_approve_licenses(u) and (not customer or user_can_access_customer(u, customer))
        elif module_type == "warranty":
            has_perm = user_can_approve_contracts(u) and (not customer or user_can_access_customer(u, customer))
        elif module_type == "hardware":
            has_perm = user_can_approve_hardware(u)

        if not has_perm:
            continue

        profile, _ = UserProfile.objects.get_or_create(user=u)
        if not profile.receive_email_notifications:
            continue

        if email_addr not in recipient_emails:
            recipient_emails.append(email_addr)

    if not recipient_emails:
        return []

    cfg = _get_smtp_config()
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    actor_name = "系統"
    if actor and getattr(actor, "is_authenticated", False):
        full_name = actor.get_full_name()
        actor_name = f"{full_name} ({actor.username})" if full_name else actor.username

    cust_display = f"{customer.name} ({customer.code})" if customer else "未綁定客戶"
    review_url = f"{cfg['site_base_url']}{review_path}"

    subject = f"[CRM 待簽核通知] {mod_label}待覆核：{target_identifier} ({customer.name if customer else ''})"
    text_body = (
        f"您好，\n\n"
        f"有一筆新的「{mod_label}」申請需要您進行覆核：\n\n"
        f"【簽核類別】{mod_label}\n"
        f"【單據/設備編號】{target_identifier}\n"
        f"【關聯客戶】{cust_display}\n"
        f"【申請同仁】{actor_name}\n"
        f"【送審時間】{now_str} (Asia/Taipei)\n"
        f"【申請摘要】\n{event_detail or '-'}\n\n"
        f"👉 點此前往審核：{review_url}\n"
    )
    html_body = f"""
    <div style="font-family: 'Inter', 'Microsoft JhengHei', sans-serif; max-width: 640px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background: #ffffff;">
      <div style="background: #d97706; color: #ffffff; padding: 16px 24px; font-size: 17px; font-weight: 700;">
        CRM 待簽核單據通知 - {escape(mod_label)}
      </div>
      <div style="padding: 24px; color: #0f172a; line-height: 1.65;">
        <div style="margin-bottom: 14px;">
          <span style="display: inline-block; background: #fef3c7; color: #b45309; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 9999px;">
            待主管覆核
          </span>
        </div>
        <h2 style="font-size: 18px; margin: 0 0 12px 0; color: #0f172a;">
          {escape(target_identifier)} <span style="font-size: 14px; color: #64748b; font-weight: 500;">— {escape(cust_display)}</span>
        </h2>
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 20px; font-size: 14px;">
          <div style="margin-bottom: 6px;"><strong>簽核類別：</strong> {escape(mod_label)}</div>
          <div style="margin-bottom: 6px;"><strong>申請同仁：</strong> {escape(actor_name)}</div>
          <div style="margin-bottom: 10px;"><strong>送審時間：</strong> {escape(now_str)}</div>
          <div style="border-top: 1px dashed #cbd5e1; padding-top: 10px; white-space: pre-wrap; color: #334155;">{escape(event_detail or '-')}</div>
        </div>
        <div style="margin-top: 20px;">
          <a href="{escape(review_url)}" style="display: inline-block; background: #d97706; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 14px;">
            前往檢視與覆核
          </a>
        </div>
      </div>
    </div>
    """

    if cfg["host"]:
        cust_code = customer.code if customer else target_identifier
        if sync:
            _send_emails_worker(subject, text_body, html_body, recipient_emails, cust_code, f"{mod_label}待覆核")
        else:
            t = threading.Thread(
                target=_send_emails_worker,
                args=(subject, text_body, html_body, recipient_emails, cust_code, f"{mod_label}待覆核"),
                daemon=True,
            )
            t.start()

    return recipient_emails


def notify_approval_decision(
    module_type: str,  # "license" | "warranty" | "hardware"
    decision: str,  # "approved" | "rejected"
    applicant,
    reviewer,
    customer,
    target_identifier: str,
    detail_text: str,
    target_path: str,
    rejection_reason: str = "",
    sync: bool = False,
) -> list[str]:
    """
    當覆核人員核准或退回單據時，自動寄信通知原開單人員 (applicant)。
    """
    if not applicant or not getattr(applicant, "is_active", False):
        return []
    if reviewer and getattr(reviewer, "is_authenticated", False) and applicant.pk == reviewer.pk:
        return []

    email_addr = (applicant.email or "").strip()
    if not email_addr:
        return []

    profile, _ = UserProfile.objects.get_or_create(user=applicant)
    if not profile.receive_email_notifications:
        return []

    module_label_map = {
        "license": "軟體授權書",
        "warranty": "維護保固合約",
        "hardware": "硬體設備出庫",
    }
    mod_label = module_label_map.get(module_type, "單據")
    is_approved = decision == "approved"
    decision_label = "已核准通過" if is_approved else "已退回修正"

    cfg = _get_smtp_config()
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    reviewer_name = "主管"
    if reviewer and getattr(reviewer, "is_authenticated", False):
        full_name = reviewer.get_full_name()
        reviewer_name = f"{full_name} ({reviewer.username})" if full_name else reviewer.username

    cust_display = f"{customer.name} ({customer.code})" if customer else "未綁定客戶"
    target_url = f"{cfg['site_base_url']}{target_path}"

    subject = f"[CRM 簽核結果] 您的{mod_label}「{target_identifier}」{decision_label}"
    reason_block = f"【退回原因】{rejection_reason}\n" if (not is_approved and rejection_reason) else ""
    text_body = (
        f"您好，\n\n"
        f"您先前送出的「{mod_label}」申請已完成覆核：\n\n"
        f"【簽核結果】{decision_label}\n"
        f"【單據/設備編號】{target_identifier}\n"
        f"【關聯客戶】{cust_display}\n"
        f"【覆核人員】{reviewer_name}\n"
        f"【覆核時間】{now_str} (Asia/Taipei)\n"
        f"{reason_block}"
        f"【單據資訊】\n{detail_text or '-'}\n\n"
        f"👉 點此查看詳情：{target_url}\n"
    )

    header_bg = "#059669" if is_approved else "#dc2626"
    badge_bg = "#ecfdf5" if is_approved else "#fef2f2"
    badge_color = "#059669" if is_approved else "#dc2626"
    reason_html = (
        f'<div style="background: #fef2f2; border-left: 4px solid #dc2626; padding: 10px 14px; margin-bottom: 14px; color: #991b1b; font-size: 14px;">'
        f'<strong>退回原因：</strong> {escape(rejection_reason)}</div>'
        if (not is_approved and rejection_reason)
        else ""
    )

    html_body = f"""
    <div style="font-family: 'Inter', 'Microsoft JhengHei', sans-serif; max-width: 640px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background: #ffffff;">
      <div style="background: {header_bg}; color: #ffffff; padding: 16px 24px; font-size: 17px; font-weight: 700;">
        CRM 簽核結果通知 - {escape(mod_label)}{escape(decision_label)}
      </div>
      <div style="padding: 24px; color: #0f172a; line-height: 1.65;">
        <div style="margin-bottom: 14px;">
          <span style="display: inline-block; background: {badge_bg}; color: {badge_color}; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 9999px;">
            {escape(decision_label)}
          </span>
        </div>
        <h2 style="font-size: 18px; margin: 0 0 12px 0; color: #0f172a;">
          {escape(target_identifier)} <span style="font-size: 14px; color: #64748b; font-weight: 500;">— {escape(cust_display)}</span>
        </h2>
        {reason_html}
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 20px; font-size: 14px;">
          <div style="margin-bottom: 6px;"><strong>覆核人員：</strong> {escape(reviewer_name)}</div>
          <div style="margin-bottom: 10px;"><strong>覆核時間：</strong> {escape(now_str)}</div>
          <div style="border-top: 1px dashed #cbd5e1; padding-top: 10px; white-space: pre-wrap; color: #334155;">{escape(detail_text or '-')}</div>
        </div>
        <div style="margin-top: 20px;">
          <a href="{escape(target_url)}" style="display: inline-block; background: {header_bg}; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 14px;">
            前往查看單據詳情
          </a>
        </div>
      </div>
    </div>
    """

    recipient_emails = [email_addr]
    if cfg["host"]:
        cust_code = customer.code if customer else target_identifier
        if sync:
            _send_emails_worker(subject, text_body, html_body, recipient_emails, cust_code, f"{mod_label}{decision_label}")
        else:
            t = threading.Thread(
                target=_send_emails_worker,
                args=(subject, text_body, html_body, recipient_emails, cust_code, f"{mod_label}{decision_label}"),
                daemon=True,
            )
            t.start()

    return recipient_emails

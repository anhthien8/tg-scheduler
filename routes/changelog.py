"""
Routes for Changelog & Release Notes feature.
Provides structured system update history for SaaS users.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/changelog", tags=["Changelog"])

CHANGELOG_DATA = [
    {
        "version": "v2.5.0",
        "date": "02/08/2026",
        "title": "🛑 Nút Bật/Tắt Account Thủ Công & Khóa AI Bảo Vệ Khách Hàng",
        "is_latest": True,
        "badge": "LATEST",
        "summary": "Bổ sung tính năng Tắt / Bật tài khoản Telegram thủ công ngắt 100% tự động hóa để test an toàn, cùng cơ chế tự động ngắt AI khi Admin nhắn tin.",
        "changes": [
            {
                "type": "feature",
                "title": "Bật / Tắt (ON/OFF) Tài Khoản Thủ Công",
                "desc": "Thêm nút '🛑 Tắt Account (OFF)' và '⚡ Bật Account (ON)' ngay trên từng thẻ tài khoản. Khi TẮT, tất cả AI Agent, Chiến dịch DM và Tự động hóa của tài khoản sẽ BỊ NGẮT HOÀN TOÀN để thử nghiệm an toàn không ảnh hưởng danh bạ cá nhân.",
                "tag": "Accounts"
            },
            {
                "type": "feature",
                "title": "Tự Động Ngắt AI Khi Admin Nhắn Tin (Human Interception)",
                "desc": "Tự động phát hiện khi bạn tự tay nhắn tin cho bất kỳ người dùng nào trên Telegram và chuyển cuộc hội thoại sang trạng thái 'needs_human' ngắt hoàn toàn AI Agent.",
                "tag": "AI Safety"
            },
            {
                "type": "improvement",
                "title": "Khóa AI Agent Chỉ Hoạt Động Theo Chiến Dịch Đang Chạy",
                "desc": "Ràng buộc AI Agent chỉ tự động trả lời người dùng thuộc các Chiến dịch đang có trạng thái 'running'. Bỏ qua 100% người dùng thuộc chiến dịch cũ đã dừng hoặc kết thúc.",
                "tag": "Campaign Isolation"
            },
            {
                "type": "improvement",
                "title": "Nâng Cấp SQLite WAL Mode & High Concurrency",
                "desc": "Bật chế độ SQLite WAL (PRAGMA journal_mode=WAL), busy_timeout=10s giúp hệ thống chạy đa tiến trình mượt mà, không bị lock database.",
                "tag": "Database"
            }
        ]
    },
    {
        "version": "v2.4.0",
        "date": "02/08/2026",
        "title": "🔥 Đa AI Agent, Global Provider & Popup Test Trực Quan",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Nâng cấp toàn bộ kiến trúc AI với tính năng quản lý Đa AI Agent (Multi-Agent BD), dùng chung Provider toàn hệ thống và giao diện thử nghiệm tương tác.",
        "changes": [
            {
                "type": "feature",
                "title": "Quản Lý Đa AI Agent (Multi-AI-Agents)",
                "desc": "Tùy biến không giới hạn các AI Agent (WEEX BD, Forex BD, CSKH...) với System Prompt, Knowledge Base, Tone và Từ khóa chuyển giao người thật riêng biệt.",
                "tag": "AI Agents"
            },
            {
                "type": "improvement",
                "title": "Cấu Hình Provider & API Key Tập Trung",
                "desc": "Tất cả AI Agent tự động thừa hưởng API Key & Provider từ mục Cài Đặt AI hệ thống, không cần nhập lại API Key cho từng Agent.",
                "tag": "System AI"
            },
            {
                "type": "improvement",
                "title": "Hỗ Trợ 9Router & OpenAI Compatible API",
                "desc": "Xử lý tương thích 100% với 9Router, Ollama, LM Studio, vLLM khi phản hồi JSON có chứa mảng tool_calls hoặc định dạng SSE.",
                "tag": "Integrations"
            },
            {
                "type": "feature",
                "title": "Popup Test AI Agent Trực Quan",
                "desc": "Thay thế hộp thoại trình duyệt bằng Modal Popup xem trước kết quả AI suy nghĩ và phản hồi trực tiếp ngay trên Dashboard.",
                "tag": "UI/UX"
            },
            {
                "type": "fix",
                "title": "Sửa Lỗi Clone Chiến Dịch DM & Dropdown Selector",
                "desc": "Khắc phục triệt để lỗi gán AI Agent khi Nhân bản chiến dịch và tự động nạp danh sách Agent mượt mà.",
                "tag": "Fixes"
            }
        ]
    },
    {
        "version": "v2.3.0",
        "date": "01/08/2026",
        "title": "👥 Bộ Công Cụ Cào & DM Tự Động Telegram Multi-Account",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Tối ưu hóa quy trình cào thành viên Deep Crawl và phân bổ tin nhắn outreach tự động giữa nhiều tài khoản Telegram gửi.",
        "changes": [
            {
                "type": "feature",
                "title": "Deep Crawl & Phân Loại Thành Viên Group/Kênh",
                "desc": "Cào toàn bộ danh sách khách hàng tiềm năng kèm phân loại Telegram Premium / Thường.",
                "tag": "Scrape & DM"
            },
            {
                "type": "improvement",
                "title": "Lọc Khách Hàng Đã DM Trong Quá Khứ",
                "desc": "Tự động phát hiện và loại trừ những Telegram User ID đã từng nhận DM từ bất kỳ chiến dịch nào trước đó.",
                "tag": "Anti-Spam"
            },
            {
                "type": "improvement",
                "title": "Multi-Account Sender Pool",
                "desc": "Phân bổ đều lượng DM giữa 17+ tài khoản Telegram gửi ngẫu nhiên để tránh bị giới hạn sáp xuất rate-limit.",
                "tag": "Telegram Engine"
            }
        ]
    },
    {
        "version": "v2.2.0",
        "date": "30/07/2026",
        "title": "🤖 AI Auto-Reply Inbox & Handover Chăm Sóc Khách Hàng",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Tích hợp AI tư vấn tự động khi có khách hàng phản hồi DM outreach.",
        "changes": [
            {
                "type": "feature",
                "title": "AI Sales Agent Auto-Reply Inbox",
                "desc": "Lắng nghe tin nhắn phản hồi, tra cứu Knowledge Base sản phẩm và tư vấn tự động theo kịch bản.",
                "tag": "Auto-Reply"
            },
            {
                "type": "improvement",
                "title": "Chuyển Giao Người Thật (Handover Keywords)",
                "desc": "Tự động gắn nhãn needs_human khi khách hàng yêu cầu gặp tư vấn viên hoặc đạt giới hạn max_replies.",
                "tag": "CRM Inbox"
            }
        ]
    },
    {
        "version": "v2.1.0",
        "date": "25/07/2026",
        "title": "🎯 Keyword DM & Tăng Tương Tác Reaction Lịch Gửi",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Phát hiện từ khóa nhóm Telegram và tự động tương tác bài viết.",
        "changes": [
            {
                "type": "feature",
                "title": "Keyword DM Tracker",
                "desc": "Phát hiện tin nhắn chứa từ khóa mục tiêu trong nhóm và gửi DM chào mời tự động.",
                "tag": "Automation"
            },
            {
                "type": "improvement",
                "title": "Tăng Tương Tác & Thả Emoji Bài Viết",
                "desc": "Tự động thả reaction cảm xúc theo kịch bản để tăng uy tín channel Telegram.",
                "tag": "Growth"
            }
        ]
    }
]


@router.get("")
async def get_changelog():
    return {
        "changelog": CHANGELOG_DATA,
        "latest_version": CHANGELOG_DATA[0]["version"] if CHANGELOG_DATA else "v1.0.0"
    }


@router.get("/latest")
async def get_latest_release():
    if not CHANGELOG_DATA:
        return {"version": "v1.0.0", "title": "Initial Release"}
    return CHANGELOG_DATA[0]

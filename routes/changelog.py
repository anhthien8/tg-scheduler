"""
Routes for Changelog & Release Notes feature.
Provides structured system update history for SaaS users.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/changelog", tags=["Changelog"])

CHANGELOG_DATA = [
    {
        "version": "v3.1.8",
        "date": "30/08/2026",
        "title": "🔄 Auto-Resume Chiến Dịch Sau SpamBot + Gọn Bảng Campaign",
        "is_latest": True,
        "badge": "LATEST",
        "summary": "Campaign bị dừng do SpamBot/PeerFlood hoặc hết daily limit giờ tự chạy lại khi account hết bị khóa. Bảng DM Campaign gọn hơn, icon có tooltip mô tả rõ ràng.",
        "changes": [
            {
                "type": "feature",
                "title": "Tự Động Chạy Tiếp Khi Hết Bị Khóa",
                "desc": "Khi tất cả sender bị SpamBot/PeerFlood chặn hoặc hết daily limit, campaign chuyển sang trạng thái 'Chờ mở khóa'. Hệ thống kiểm tra mỗi 15 phút, tự chạy tiếp ngay khi có account sẵn sàng.",
                "tag": "Auto Resume"
            },
            {
                "type": "feature",
                "title": "Toggle Auto-Resume Trên Từng Campaign",
                "desc": "Bật/tắt 'Tự Động Chạy Tiếp' riêng cho từng chiến dịch trong modal tạo/sửa, hoặc nhanh trên dòng campaign đang chờ mở khóa.",
                "tag": "Cấu hình"
            },
            {
                "type": "improvement",
                "title": "Bảng Campaign Gọn Hơn",
                "desc": "Gộp cột Tên + Nguồn, badge trạng thái đồng nhất kích thước, thống kê chi tiết chuyển vào tooltip, nút hành động chỉ icon có mô tả khi rê chuột.",
                "tag": "UI"
            },
            {
                "type": "improvement",
                "title": "Icon Có Tooltip Mô Tả",
                "desc": "Icon thống kê trên dashboard (📤 DM hôm nay, ✅ tỷ lệ thành công, ⚠️ số lần flood, điểm sức khỏe) giờ hiển thị mô tả khi rê chuột.",
                "tag": "UI"
            }
        ]
    },
    {
        "version": "v3.1.7",
        "date": "29/08/2026",
        "title": "👤 Hiển Thị Rõ Nick Phụ Đang Chat Với KOL",
        "is_latest": False,
        "badge": "",
        "summary": "Modal Xem chat nay hiển thị tên tài khoản phụ đang dùng để chat với KOL, không chỉ hiện Acc #ID. Bubble AI/chat tay cũng ghi rõ account gửi tin.",
        "changes": [
            {
                "type": "improvement",
                "title": "Tên Account Trong Tiêu Đề Chat",
                "desc": "Tiêu đề modal đổi từ 'Chat với KOL (Acc #6)' sang 'Tên account đang chat với KOL', giúp biết ngay nick phụ nào đang xử lý lead.",
                "tag": "Chat UX"
            },
            {
                "type": "improvement",
                "title": "Bubble AI / Chat Tay Ghi Rõ Account Gửi",
                "desc": "Label tin nhắn AI và tin chat tay nay hiển thị tên account phụ thay vì chỉ ghi 'AI Sales Agent' hoặc 'Bạn'.",
                "tag": "Account Identity"
            },
            {
                "type": "fix",
                "title": "API Lead List Trả account_name",
                "desc": "Query ai_followup_chats join accounts để frontend nhận được tên account phụ tương ứng với account_id.",
                "tag": "API"
            }
        ]
    },
    {
        "version": "v3.1.6",
        "date": "29/08/2026",
        "title": "🕒 Hiển Thị Ngày Giờ Đầy Đủ Trong Lịch Sử Chat",
        "is_latest": False,
        "badge": "",
        "summary": "Modal Xem chat trong Lead & AI Follow-Up nay hiển thị đầy đủ ngày/tháng/năm và giờ Việt Nam cho từng tin nhắn, đồng thời sắp xếp lịch sử theo timestamp thật.",
        "changes": [
            {
                "type": "fix",
                "title": "Timestamp Đầy Đủ Cho Tin Nhắn",
                "desc": "Thay toLocaleTimeString bằng toLocaleString vi-VN, timeZone Asia/Ho_Chi_Minh, định dạng dd/mm/yyyy HH:mm:ss để biết tin nhắn thuộc ngày nào.",
                "tag": "Chat History"
            },
            {
                "type": "fix",
                "title": "Sắp Xếp Tin Nhắn Theo Thời Gian Thật",
                "desc": "Lịch sử chat được sort client-side theo timestamp tăng dần trước khi render, tránh trường hợp tin 12:15 đứng trước tin 11:24 nếu dữ liệu trả về chưa đúng thứ tự.",
                "tag": "Ordering"
            }
        ]
    },
    {
        "version": "v3.1.5",
        "date": "29/08/2026",
        "title": "🤖 Bật/Tắt AI Hàng Loạt Cho Lead",
        "is_latest": False,
        "badge": "",
        "summary": "Chọn nhiều lead trong Lead & AI Follow-Up rồi bật hoặc tắt AI cho tất cả cùng lúc ngay trên action bar.",
        "changes": [
            {
                "type": "feature",
                "title": "Bulk AI Control",
                "desc": "Action bar khi chọn lead có thêm hai nút Bật AI và Tắt AI. Hệ thống yêu cầu xác nhận, cập nhật song song và báo tổng số thành công/thất bại.",
                "tag": "AI Follow-Up"
            }
        ]
    },
    {
        "version": "v3.1.4",
        "date": "29/08/2026",
        "title": "✅ Việc Cần Làm Ngay Trên Trang Tổng Quan",
        "is_latest": False,
        "badge": "",
        "summary": "Dashboard có thêm checklist Việc cần làm để ghi nhanh công việc vận hành, đánh dấu hoàn thành và xóa trực tiếp mà không cần rời trang Tổng quan.",
        "changes": [
            {
                "type": "feature",
                "title": "Checklist Việc Cần Làm",
                "desc": "Thêm việc mới, tick hoàn thành (gạch ngang) và xóa từng việc trực tiếp trên dashboard. Dữ liệu lưu persistent trong Settings DB, giữ nguyên sau restart.",
                "tag": "Dashboard"
            },
            {
                "type": "improvement",
                "title": "Dashboard Responsive 3 Cột",
                "desc": "Desktop rộng hiển thị Việc cần làm · Lịch gửi · Sức khỏe tài khoản trên cùng hàng. Tự chuyển 2 cột hoặc 1 cột trên màn hình nhỏ.",
                "tag": "Responsive UX"
            }
        ]
    },
    {
        "version": "v3.1.3",
        "date": "29/08/2026",
        "title": "📦 Thêm Hàng Loạt Tài Khoản Telegram",
        "is_latest": False,
        "badge": "",
        "summary": "Modal Thêm tài khoản nay có chế độ Hàng loạt: dán danh sách số điện thoại (kèm proxy tuỳ chọn), app tự gửi OTP lần lượt và chờ bạn nhập từng mã — không phải mở modal nhiều lần.",
        "changes": [
            {
                "type": "feature",
                "title": "Bulk Add — Thêm Nhiều Tài Khoản 1 Lần",
                "desc": "Tab 'Hàng loạt' trong modal: nhập mỗi dòng 'số điện thoại | proxy', app xử lý tuần tự, hiển thị tiến độ và nút Bỏ qua cho từng số. Kết quả tổng hợp (OK / lỗi) sau khi xong.",
                "tag": "Bulk Import"
            },
            {
                "type": "improvement",
                "title": "OTP Step Hiển Thị Số Điện Thoại Đang Xử Lý",
                "desc": "Label mã OTP nay kèm theo số điện thoại hiện tại để tránh nhầm lẫn khi thêm nhiều tài khoản liên tiếp.",
                "tag": "UX"
            }
        ]
    },
    {
        "version": "v3.1.2",
        "date": "29/08/2026",
        "title": "📡 Auto-Forward Bài Channel KOL Theo Assignment",
        "is_latest": False,
        "badge": "",
        "summary": "Tự động forward bài mới từ channel nguồn (VD @weexkolglobal) đến KOL onboarded theo phân công region/campaign. Mỗi KOL nhận link WEEX riêng gắn vipCode của họ.",
        "changes": [
            {
                "type": "feature",
                "title": "KOL Channel Watcher — Auto-Forward Bài Mới",
                "desc": "Listener nhận bài từ channel nguồn, lọc KOL theo tag [vn]/[global]/[region:kr]/[campaign:summer] trong 3 dòng đầu bài. Bài không tag → gửi cho mọi KOL đang bật.",
                "tag": "KOL Distribution"
            },
            {
                "type": "feature",
                "title": "Dedup Broadcast — Không Gửi Lại Bài Cũ Sau Restart",
                "desc": "Bảng kol_broadcast_log ghi nhận message_id đã gửi. Restart server không gây gửi trùng bài.",
                "tag": "Reliability"
            },
            {
                "type": "feature",
                "title": "vipCode Cá Nhân Hoá Mỗi KOL",
                "desc": "Link WEEX trong bài tự động thay vipCode riêng của từng KOL. KOL thiếu vipCode và bài có link WEEX → skip, ghi log.",
                "tag": "Personalization"
            },
            {
                "type": "feature",
                "title": "Cài Đặt Channel Trong UI",
                "desc": "Tab Cấu hình AI → card Auto-forward bài channel: bật/tắt, đặt channel nguồn, chọn account listen. Thay đổi có hiệu lực ngay không cần restart.",
                "tag": "Settings"
            }
        ]
    },
    {
        "version": "v3.1.1",
        "date": "29/08/2026",
        "title": "🤖 AI Không Tự Bật Khi Nick Nội Bộ Nhắn Nhau",
        "is_latest": False,
        "badge": "",
        "summary": "Khắc phục lỗi AI follow-up kích hoạt khi 2 nick nội bộ (nick chính / nick phụ) nhắn tin test với nhau, gây phát sinh reply giả và đôi khi hướng sai về account chính.",
        "changes": [
            {
                "type": "fix",
                "title": "Guard Sender Nội Bộ — Skip AI Hoàn Toàn",
                "desc": "Kiểm tra sender_id có thuộc danh sách account đang quản lý không (_me_cache). Nếu có → return ngay, không append chat, không gọi AI, không reply.",
                "tag": "AI Guard"
            }
        ]
    },
    {
        "version": "v3.1.0",
        "date": "29/08/2026",
        "title": "🎯 KOL Distribution Pipeline: Phân Công, Bulk Send & Personalization",
        "is_latest": False,
        "badge": "",
        "summary": "Hệ thống phân phối nội dung KOL hoàn chỉnh: admin gán region/campaign một lần, gửi hàng loạt với link WEEX cá nhân hoá vipCode. AI warm-up trung thực, cấm bịa dữ liệu fintech.",
        "changes": [
            {
                "type": "feature",
                "title": "KOL Profile: affiliate_link, vipCode, phân công region/campaign",
                "desc": "Lưu persistent, sửa được trong modal Hồ sơ & phân công. Chỉ sửa URL weex.com, giữ nguyên query params khác.",
                "tag": "KOL Management"
            },
            {
                "type": "feature",
                "title": "Bulk Send Có Lọc vipCode",
                "desc": "Chọn nhiều lead → Gửi hàng loạt. Tự skip KOL thiếu vipCode khi bài có link WEEX. Trả về sent/skipped/errors.",
                "tag": "Bulk Send"
            },
            {
                "type": "fix",
                "title": "AI Warm-Up Trung Thực — Truthfulness Mandate",
                "desc": "Cấm tuyệt đối bịa referral, UID, campaign, hoa hồng, volume, follower, thu nhập. 1–3 lượt đầu chỉ warm-up, không pitch. Rule handover nội bộ — không hướng KOL sang @weexwill.",
                "tag": "AI Safety"
            },
            {
                "type": "fix",
                "title": "Thông Báo Handover: Xuống Dòng Thật, Ẩn Field Trống, Dedup 5 Phút",
                "desc": "Sửa lỗi \\\\n literal trong Saved Messages. Ẩn hàng chưa có dữ liệu. Không gửi trùng cùng KOL trong 5 phút.",
                "tag": "Handover Alert"
            },
            {
                "type": "improvement",
                "title": "Card Tài Khoản Telegram Compact",
                "desc": "Gộp badge Online/status thành chấm màu trên avatar. Meta (ID, giới hạn DM) inline. AI Agent select 1 dòng. Nút action nhỏ gọn — card đều chiều cao hơn.",
                "tag": "UI"
            }
        ]
    },
    {
        "version": "v3.0.4",
        "date": "26/08/2026",
        "title": "🔧 Gia Cố Hệ Thống: Chống Cache Stampede, Tối Ưu GZip & Ổn Định Bộ Nhớ",
        "is_latest": False,
        "badge": "",
        "summary": "Bản vá tăng cường độ ổn định từ đợt rà soát Adversarial Hardening: cache analytics chống thundering-herd với fallback dữ liệu cũ, GZip bỏ qua file nhị phân đã nén sẵn, giới hạn bộ nhớ cache LRU và dọn dẹp toàn bộ test suite.",
        "changes": [
            {
                "type": "fix",
                "title": "Chống Cache Stampede (Singleflight)",
                "desc": "Cache analytics nay dùng khóa per-key: khi nhiều request cùng truy cập một key lạnh, chỉ 1 truy vấn DB chạy, các request còn lại chờ và dùng chung kết quả — giảm tải DB tức thì.",
                "tag": "Cache Hardening"
            },
            {
                "type": "fix",
                "title": "Fallback Dữ Liệu Cũ Khi DB Lỗi (Stale-While-Revalidate)",
                "desc": "Nếu truy vấn analytics thất bại, hệ thống tự động trả về dữ liệu cache gần nhất thay vì báo lỗi 500, đảm bảo dashboard luôn hiển thị.",
                "tag": "Resilience"
            },
            {
                "type": "fix",
                "title": "Giới Hạn Bộ Nhớ Cache (LRU 256 keys)",
                "desc": "AsyncTTLCache nay có giới hạn tối đa 256 key với cơ chế LRU eviction, ngăn chặn rò rỉ bộ nhớ khi có quá nhiều tham số truy vấn khác nhau.",
                "tag": "Memory Safety"
            },
            {
                "type": "fix",
                "title": "GZip Bỏ Qua File Nhị Phân (ADV-01)",
                "desc": "Middleware GZip không còn nén ảnh/video/âm thanh/zip/octet-stream — những định dạng đã nén sẵn. Tiết kiệm CPU và tránh tăng kích thước payload vô ích.",
                "tag": "Performance"
            },
            {
                "type": "fix",
                "title": "Dọn Dẹp & Siết Chặt Test Suite",
                "desc": "Loại bỏ request trùng lặp trong test e2e, đổi tên route handler gây cảnh báo pytest, bổ sung assertion kiểm tra ảnh không bị gzip và thêm style toast warning còn thiếu.",
                "tag": "QA Cleanup"
            }
        ]
    },
    {
        "version": "v3.0.3",
        "date": "05/08/2026",
        "title": "🛡️ Chặn Triệt Để 100% Tất Cả Các Loại Telegram Bot & Username Chứa 'bot'",
        "is_latest": False,
        "badge": "",
        "summary": "Nâng cấp bộ lọc bot đa tầng: kiểm tra thuộc tính .bot, chặn tất cả username/tên chứa từ 'bot' (case-insensitive) ở cả Keyword DM Watcher và Inbox AI Agent.",
        "changes": [
            {
                "type": "fix",
                "title": "Bộ Lọc Telegram Bot Triệt Để (Strict Bot Filter)",
                "desc": "Bổ sung hàm is_bot_account kiểm tra thuộc tính .bot, quét toàn bộ username chứa 'bot' (ví dụ: @...bot, @..._bot, @...bot_...), tên hiển thị chứa 'bot' và chặn ngay lập tức từ tầng Keyword Watcher đến Inbox AI Agent.",
                "tag": "Bot Ignorance Guard"
            },
            {
                "type": "feature",
                "title": "Tự Động Dọn Dẹp Cơ Sở Dữ Liệu Bot",
                "desc": "Tự động quét và chuyển trạng thái bot_ignored cho toàn bộ các cuộc hội thoại cũ có chứa chữ 'bot' trong username, đảm bảo AI Agent không bao giờ theo đuổi hay nhắn tin lại cho Bot.",
                "tag": "Database Cleanup"
            }
        ]
    },
    {
        "version": "v3.0.2",
        "date": "04/08/2026",
        "title": "⚡ Tự Động Join Nhóm Cho Các Tài Khoản Gửi DM & Sửa Lỗi Modal 500 Error",
        "is_latest": False,
        "badge": "",
        "summary": "Tự động kích hoạt Join nhóm Telegram đối với tất cả tài khoản gửi tin nhắn chưa tham gia nhóm target. Khắc phục dứt điểm lỗi Internal Server Error khi tìm kiếm nhóm.",
        "changes": [
            {
                "type": "feature",
                "title": "Auto-Join Nhóm Tự Động Hóa Toàn Diện",
                "desc": "Tự động cho các tài khoản gửi DM join vào nhóm target khi tạo/sửa Keyword Rule hoặc khi quét được tin nhắn nhóm mà tài khoản chưa có mặt.",
                "tag": "Auto-Join Engine"
            },
            {
                "type": "fix",
                "title": "Khắc Phục Lỗi Internal Server Error Modal",
                "desc": "Bổ sung cơ chế fallback an toàn cho API get_chats, ngăn ngừa lỗi 500 khi tài khoản bị đứt kết nối hoặc không phản hồi.",
                "tag": "API Reliability"
            }
        ]
    },
    {
        "version": "v3.0.1",
        "date": "03/08/2026",
        "title": "⏰ Tự Động Tiếp Quản Trả Lời Thay Người Thật Sau 60 Phút Trôi Qua (60m Human Timeout Auto-Resume)",
        "is_latest": False,
        "badge": "",
        "summary": "Tự động kích hoạt AI Agent tiếp quản lại phiên chat nếu người thật đã nhắn tin tiếp quản trước đó nhưng sau 60 phút người dùng nhắn lại mà người thật vẫn chưa phản hồi.",
        "changes": [
            {
                "type": "feature",
                "title": "Thời Gian Chờ Tiếp Quản Tự Động 60 Phút",
                "desc": "Theo dõi thời điểm người thật can thiệp thủ công (needs_human). Nếu quá 60 phút người thật không trả lời tin nhắn của khách, AI Agent sẽ tự động chuyển lại active và nhảy vào trả lời thay.",
                "tag": "AI Human Coexistence"
            }
        ]
    },
    {
        "version": "v3.0.0",
        "date": "03/08/2026",
        "title": "💎 Phân Loại Lead Tier A/B/C, Intent Scoring & Drip Follow-Up (Crypto BD Power Pack)",
        "is_latest": False,
        "badge": "",
        "summary": "Nâng cấp toàn bộ hệ thống AI Agent chuyên sâu cho Business Development sàn Crypto: tự động chấm điểm Intent (0-100), xếp hạng Lead Tier A/B/C, tóm tắt bối cảnh nhu cầu khách hàng và tự động chạy chuỗi Drip Follow-up sau 48h im lặng.",
        "changes": [
            {
                "type": "feature",
                "title": "Phân Loại Lead Tier A/B/C & Intent Score 0-100",
                "desc": "Tự động trích xuất intent_score và phân hạng Tier A (Hot Lead), Tier B, Tier C trực tiếp từ cuộc hội thoại để BD người thật tập trung chốt deal lớn.",
                "tag": "Lead Qualification"
            },
            {
                "type": "feature",
                "title": "Agent Mẫu '🤖 Crypto Exchange BD Pro'",
                "desc": "Cung cấp sẵn Agent mẫu với Knowledge Base thương lượng hoa hồng RevShare 50%-75%, ma trận phí Maker 0.02%/Taker 0.06% và kịch bản chốt meeting.",
                "tag": "AI Presets"
            },
            {
                "type": "feature",
                "title": "Tự Động Bám Đổi (Drip Follow-up Engine)",
                "desc": "Tự động quét các cuộc trò chuyện bị ngưng sau 48h/120h để gửi tin nhắn gợi mở sự kiện HOT ($50k pool, VIP discounts) giúp hồi sinh Lead im lặng.",
                "tag": "Lead Nurturing"
            },
            {
                "type": "ui",
                "title": "Giao Diện AI Context Summary & Tier Badges",
                "desc": "Hiển thị Badge ⭐ Tier A (Hot), thanh điểm Intent Score và nút '📋 Tóm Tắt Context' giúp BD người thật nắm ngay tâm lý khách trong 3 giây.",
                "tag": "Dashboard UX/UI"
            }
        ]
    },
    {
        "version": "v2.9.3",
        "date": "03/08/2026",
        "title": "🤖 Tự Động Né & Bỏ Qua Telegram Bots (Telegram Bot Auto-Ignorance)",
        "is_latest": False,
        "badge": "",
        "summary": "Tự động phát hiện và bỏ qua toàn bộ tin nhắn từ Telegram Bot (SpamBot, InfoBot, BotFather, RoseBot...), ngăn chặn tuyệt đối tình trạng AI Agent tự nhắn tin/trả lời bot.",
        "changes": [
            {
                "type": "feature",
                "title": "Bộ Lọc Nhận Diện Telegram Bot Tự Động",
                "desc": "Bổ sung hàm is_bot_account kiểm tra thuộc tính .bot, đuôi username '%bot', ID hệ thống (777000, 178220800...) để chặn đứng tương tác với Bot ngay tại tầng Inbox.",
                "tag": "AI Safety & Anti-Spam"
            },
            {
                "type": "fix",
                "title": "Dọn Dẹp Phiên Chat Bot Hiện Có",
                "desc": "Tự động chuyển các phiên trò chuyện hiện có với bot sang trạng thái 'bot_ignored' trong cơ sở dữ liệu để đảm bảo AI Agent không bao giờ phản hồi.",
                "tag": "Database Cleanup"
            }
        ]
    },
    {
        "version": "v2.9.2",
        "date": "03/08/2026",
        "title": "🎨 Tái Thiết Kế Giao Diện Thẻ Tài Khoản Telegram (Account Cards Redesign)",
        "is_latest": False,
        "badge": "",
        "summary": "Nâng cấp giao diện danh sách Telegram Accounts theo chuẩn Glassmorphic hiện đại, sắp xếp khoa học thông tin Avatar, Badge trạng thái, Giới hạn DM/ngày và Khung chọn AI Agent phụ trách.",
        "changes": [
            {
                "type": "ui",
                "title": "Tái Thiết Kế Cấu Trúc Card Khoa Học",
                "desc": "Cải thiện visual card với Avatar kèm chấm Indicator trạng thái (Online / Off / Disconnected), gom nhóm badge góc phải và dải thông số kỹ thuật (ID / Giới hạn DM/ngày).",
                "tag": "Accounts UI/UX"
            },
            {
                "type": "improvement",
                "title": "Tối Ưu Hóa Nút Thao Tác & Khung AI Agent",
                "desc": "Tách riêng khung '🤖 AI Agent Phụ Trách' sang trọng và tinh chỉnh nút '▶️ Bật Account' / '⏸️ Tắt Account' gọn gàng, tránh nhầm lẫn cho người dùng.",
                "tag": "User Experience"
            }
        ]
    },
    {
        "version": "v2.9.1",
        "date": "03/08/2026",
        "title": "🛑 Tính Năng Tự Động Nhường Quyền Khi Người Thật Nhắn Tin (Human Takeover Interception)",
        "is_latest": False,
        "badge": "",
        "summary": "Tự động phát hiện khi chủ tài khoản Telegram tự tay nhắn tin với khách hàng (trên ứng dụng Telegram điện thoại/máy tính), AI Agent sẽ ngay lập tức tự động nhường quyền và giữ yên lặng để không làm gián đoạn cuộc trò chuyện.",
        "changes": [
            {
                "type": "feature",
                "title": "Phát Hiện Người Thật Trả Lời Thủ Công (Human Outgoing Interception)",
                "desc": "Theo dõi các tin nhắn đi (outgoing) phát xuất từ chủ tài khoản. Khi phát hiện người thật nhắn tin thủ công cho khách, hệ thống tự động gán nhãn status='needs_human' cho cuộc hội thoại đó.",
                "tag": "AI Human Coexistence"
            },
            {
                "type": "improvement",
                "title": "Phân Biệt Tin Nhắn Từ AI Script vs Tin Nhắn Từ Người Thật",
                "desc": "Sử dụng bộ nhớ theo dõi _pending_ai_sends để phân biệt chính xác đâu là tin nhắn do AI Agent gửi và đâu là tin nhắn do chủ tài khoản tự gõ bằng tay.",
                "tag": "Telethon Event Engine"
            }
        ]
    },
    {
        "version": "v2.9.0",
        "date": "03/08/2026",
        "title": "🤖 Hệ Thống Multi-AI-Agent, 9Router Local Proxy & Dynamic Fallback Tự Động",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Phát hành hệ thống Quản lý Đa AI Agents chuyên biệt cho sales/BD, tích hợp kết nối 9Router local proxy, tự động xử lý định dạng Telegram HTML, phản hồi đa ngôn ngữ theo khách hàng và luồng Fallback đa tầng 24/7.",
        "changes": [
            {
                "type": "feature",
                "title": "Hệ Thống Quản Lý Đa AI Agents (Multi-AI-Agent Architecture)",
                "desc": "Cho phép khởi tạo nhiều AI Agent riêng biệt (BD WEEX, CSKH, Sales Negotiator) với System Prompt, Knowledge Base, Provider và Model hoàn toàn độc lập.",
                "tag": "AI System"
            },
            {
                "type": "feature",
                "title": "Tích Hợp 9Router Local Proxy & Namespace Models",
                "desc": "Hỗ trợ kết nối tới 9Router local proxy (http://127.0.0.1:20128/v1) với hơn 93+ model AI cao cấp (im/claude-opus-4.7, ag/gemini-3-flash, cd/codex-5.6), tự động lắp ráp SSE Server-Sent Events stream.",
                "tag": "9Router API"
            },
            {
                "type": "feature",
                "title": "Chuỗi Tự Động Fallback API Đa Tầng (Multi-Provider Fallback Chain)",
                "desc": "Tự động dự phòng 24/7: Khi Provider chính (9Router) bị lỗi hoặc nghẽn, hệ thống tự động nhảy sang Gemini -> Groq -> OpenAI -> DeepSeek mà không làm ngắt quãng hội thoại.",
                "tag": "AI Reliability"
            },
            {
                "type": "improvement",
                "title": "Tự Động Nhận Diện & Phản Hồi Ngôn Ngữ Khách Hàng (Dynamic Language Matching)",
                "desc": "AI tự động phân tích ngôn ngữ của khách hàng (Tiếng Trung, Tiếng Anh, Tiếng Việt...) và phản hồi bằng đúng ngôn ngữ mẹ đẻ của khách hàng.",
                "tag": "NLP & Language"
            },
            {
                "type": "bugfix",
                "title": "Bộ Lọc Telegram HTML Sanitizer Chuyên Biệt",
                "desc": "Tự động chuyển đổi các thẻ HTML trang web (<p>, <div>, <br>, <li>) và chuỗi escaped '\\n' thành ngắt dòng tự nhiên và thẻ Telegram chuẩn (<b>, <i>), khắc phục vỡ giao diện tin nhắn.",
                "tag": "Telegram Engine"
            }
        ]
    },
    {
        "version": "v2.7.4",
        "date": "03/08/2026",
        "title": "🛡️ Tổng Kiểm Thử Toàn Hệ Thống (Full System QA/QC Audit & Hardening)",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Hoàn tất tổng kiểm thử toàn bộ hệ thống từ UI/UX, Frontend DOM safety, backend connection pool đến việc bọc try-except các API endpoint và sửa lỗi kiểu dữ liệu ở auto-resume.",
        "changes": [
            {
                "type": "bugfix",
                "title": "Sửa Lỗi Kiểu Dữ Liệu Auto-Resume DM Campaigns (`main.py`)",
                "desc": "Khắc phục triệt để nguy cơ `AttributeError: 'bool' object has no attribute 'done'` khi tiến trình auto-resume kiểm tra danh sách chiến dịch đang chạy sau khi restart server.",
                "tag": "Backend Lifespan"
            },
            {
                "type": "improvement",
                "title": "Bọc Xử Lý Lỗi Exception Cho Các Endpoint Xóa (`API Hardening`)",
                "desc": "Bổ sung khối `try...except` và trả về `HTTPException(500)` chuẩn cho các endpoint xóa Scrape Job (`routes/members.py`) và xóa Invite Campaign (`routes/invite.py`).",
                "tag": "API Security"
            },
            {
                "type": "feature",
                "title": "Xác Nhận Đạt 100% Tiêu Chuẩn QA/QC",
                "desc": "Đã kiểm tra 71 file Python, 8 file JS, 0 lỗi DOM null dereference, 0 lệnh `showToast` sai quy tắc và 0 tham chiếu Zalo dư thừa.",
                "tag": "System Audit"
            }
        ]
    },
    {
        "version": "v2.7.3",
        "date": "03/08/2026",
        "title": "🎯 Sửa Triệt Để Lỗi Báo Tiến Độ Gửi (Progress Tracking & Real-Time Sync)",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Khắc phục triệt để tình trạng lệch con số tiến độ và thanh phần trăm (progress bar), đồng thời chuyển sang cập nhật tiến độ real-time theo từng tin nhắn ở backend.",
        "changes": [
            {
                "type": "improvement",
                "title": "Đồng Bộ Con Số Tiến Độ & Thanh Phần Trăm (UI Progress Sync)",
                "desc": "Hiển thị rõ ràng tổng số target đã xử lý `${processed}/${total} (${progress}%)` để con số ở tiêu đề khớp 100% với thanh phần trăm tím (bao gồm cả tin nhắn gửi thành công, lỗi và bỏ qua qua trùng lặp).",
                "tag": "Campaign UI"
            },
            {
                "type": "feature",
                "title": "Cập Nhật Tiến Độ Real-Time Theo Từng Tin Nhắn (Backend)",
                "desc": "Loại bỏ cơ chế lưu DB mỗi 5 tin nhắn. Giờ đây backend ghi nhận và cập nhật DB tức thì sau mỗi tin nhắn (sent, fail, skip), giúp người dùng xem tiến độ chính xác từng giây.",
                "tag": "Campaign Engine"
            },
            {
                "type": "improvement",
                "title": "Tự Động Cập Nhật Lại Hàng Khi Tiến Độ Thay Đổi",
                "desc": "Cập nhật logic so sánh ở Frontend (`data-sent`, `data-failed`, `data-skipped`) để giao diện bảng tự động re-render mượt mà mỗi khi số lượng tin nhắn thay đổi.",
                "tag": "Frontend State"
            }
        ]
    },
    {
        "version": "v2.7.2",
        "date": "03/08/2026",
        "title": "🎨 Thiết Kế Lại Giao Diện & Tối Ưu UI/UX Modal Tạo DM Campaign",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Sửa triệt để lỗi vỡ layout công tắc chuyển đổi (toggle), tối ưu spacing, typography và cấu trúc các ô nhập liệu giúp trải nghiệm tạo chiến dịch chuyên nghiệp & mượt mà hơn.",
        "changes": [
            {
                "type": "improvement",
                "title": "Khắc Phục Lỗi Vỡ Layout Toggle Switch",
                "desc": "Khắc phục triệt để tình trạng công tắc toggle bị đè/tràn lên chữ khi thu nhỏ layout nhờ bổ sung `flex-shrink: 0` và thiết kế lại slider mượt mà với hiệu ứng neon glow.",
                "tag": "UI/UX"
            },
            {
                "type": "feature",
                "title": "Cấu Trúc Modal Thẻ Card Hiện Đại & Trực Quan",
                "desc": "Phân chia rõ ràng thành các phân vùng thẻ Card riêng biệt (Thông tin chung, Giãn cách Anti-Ban, Cấu hình Trí Tuệ Nhân Tạo AI) giúp thao tác cài đặt nhanh chóng và trực quan.",
                "tag": "Campaign Modal"
            },
            {
                "type": "improvement",
                "title": "Tối Ưu Khung Soạn Thảo Tin Nhắn Textarea",
                "desc": "Loại bỏ tay cầm kéo giãn xấu của trình duyệt, áp dụng viền mờ tối, phông chữ 13.5px dễ đọc và hiệu ứng viền phát sáng khi focus.",
                "tag": "Form Design"
            }
        ]
    },
    {
        "version": "v2.7.1",
        "date": "03/08/2026",
        "title": "⚡ Tự Động Khôi Phục & Tiếp Tục Gửi DM Sau Khi Server Khởi Động Lại",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Tự động phát hiện và khôi phục các Chiến dịch DM đang ở trạng thái 'running' mỗi khi app restart, khắc phục triệt để tình trạng campaign bị dừng ngầm.",
        "changes": [
            {
                "type": "feature",
                "title": "Tự Động Auto-Resume DM Campaigns",
                "desc": "Mỗi khi ứng dụng restart/reload, hệ thống sẽ tự động tìm tất cả các DM Campaign đang ở trạng thái 'running' để tiếp tục gửi tin nhắn mà không cần phải nhấn nút chạy lại.",
                "tag": "Campaign Engine"
            },
            {
                "type": "improvement",
                "title": "Sửa Nguyên Nhân Bị Trùng Trạng Thái Running",
                "desc": "Cập nhật hàm `start_campaign` cho phép kích hoạt lại tiến trình gửi nếu tiến trình background cũ đã kết thúc hoặc bị gián đoạn.",
                "tag": "DM Dispatcher"
            }
        ]
    },
    {
        "version": "v2.7.0",
        "date": "02/08/2026",
        "title": "🌐 AI Auto Native Language Outreach — Tự Động Dịch DM Theo Tiếng Bản Địa Của Khách Hàng",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "AI tự động phát hiện ngôn ngữ & quốc gia của từng member (Trung Quốc, Nga, Thổ Nhĩ Kỳ, Việt Nam...) để chuyển đổi tin nhắn outreach sang tiếng mẹ đẻ của họ, tăng Reply Rate 3-5x.",
        "changes": [
            {
                "type": "feature",
                "title": "Tự Động Phát Hiện & Dịch Tiếng Bản Địa (Auto Native Language)",
                "desc": "Khi bật tùy chọn '🌐 Tự Động Dịch Ngôn Ngữ Bản Địa' trong Chiến dịch DM, AI sẽ tự động phân tích `lang_code` và font chữ tên của từng member để dịch & biến tấu câu chào/nội dung outreach sang đúng tiếng mẹ đẻ (Trung 🇨🇳, Nga 🇷🇺, Thổ 🇹🇷, Việt 🇻🇳, Hàn 🇰🇷...).",
                "tag": "Localization"
            },
            {
                "type": "improvement",
                "title": "Thu Thập Telegram Language Code (lang_code)",
                "desc": "Tự động thu thập thuộc tính `lang_code` từ Telegram API khi cào member nhóm và lưu trữ trong cơ sở dữ liệu `scraped_members`.",
                "tag": "Member Scraping"
            },
            {
                "type": "improvement",
                "title": "Bảo Toàn Tên Thương Hiệu, Link & Mã Giảm Giá",
                "desc": "Giữ nguyên 100% tất cả liên kết (URL), username @, tên thương hiệu (WEEX, Blofin...) và mã giới thiệu khi thực hiện dịch thuật bản địa hóa.",
                "tag": "AI Remix"
            }
        ]
    },
    {
        "version": "v2.6.0",
        "date": "02/08/2026",
        "title": "🤖 Chế Độ AI Care Auto-Pilot Theo Từng Tài Khoản Telegram",
        "is_latest": False,
        "badge": "STABLE",
        "summary": "Cho phép gán AI Agent trực tiếp cho từng Tài Khoản Telegram để tự động trả lời khách hàng khi bạn rời máy tính hoặc off.",
        "changes": [
            {
                "type": "feature",
                "title": "AI Care Auto-Pilot Cho Từng Tài Khoản",
                "desc": "Thêm menu chọn '🤖 AI Care Account' trên mỗi thẻ Telegram Account. Khi bạn off máy, AI Agent được gán sẽ tự động phụ trách trả lời, tư vấn khách hàng phản hồi tự nhiên.",
                "tag": "AI Care"
            },
            {
                "type": "improvement",
                "title": "Phân Cấp Phản Hỏi Thông Minh (Campaign -> Account)",
                "desc": "Ưu tiên AI Agent của Chiến dịch DM nếu user thuộc chiến dịch running. Nếu không, tự động chuyển sang AI Agent của chính Account Telegram đó.",
                "tag": "AI Routing"
            },
            {
                "type": "feature",
                "title": "Tự Động Bàn Giao (Auto-Handover)",
                "desc": "Vẫn giữ nguyên cơ chế bảo vệ: Ngay khi bạn mở Telegram tự nhắn tin thủ công cho khách, hệ thống sẽ lập tức ngắt AI Agent để bạn chủ động hội thoại.",
                "tag": "Safety"
            }
        ]
    },
    {
        "version": "v2.5.0",
        "date": "02/08/2026",
        "title": "🛑 Nút Bật/Tắt Account Thủ Công & Khóa AI Bảo Vệ Khách Hàng",
        "is_latest": False,
        "badge": "STABLE",
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

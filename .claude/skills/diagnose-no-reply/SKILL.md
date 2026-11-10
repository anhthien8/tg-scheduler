---
name: diagnose-no-reply
description: Tìm lý do AI không trả lời một chat Telegram. Dùng khi người dùng hỏi "sao AI không rep", gửi ảnh chụp một chat không được trả lời, hoặc nghi nick không nhận tin.
---

# Vì sao AI không trả lời?

Bạn thường không đọc được log và database của máy đang chạy. Nói rõ điều đó, rồi đi theo các cổng trong `dm_reply_tracker.py` (hàm `_make_handler` và `generate_and_send_ai_reply_for_chat`), xếp theo khả năng với ngữ cảnh của chat.

| Dấu hiệu trong chat / dashboard | Nguyên nhân | Dòng log để tìm |
|---|---|---|
| Admin vừa nhắn tay trong 12 giờ | Human takeover | `Human admin active` |
| Chat ở trạng thái needs_human / paused_admin | Đã chuyển cho người | `status '...' — recorded msg, skipping` |
| Stage paused / signed, hoặc KOL đã qua bước prospect | Admin giữ chat | `AI silent` |
| Nick chưa gắn AI Agent | Agent tắt | `AI Agent disabled (Tắt)` |
| Username chứa `_bot`, hoặc Telegram gắn cờ bot | Bị coi là bot | `Ignored bot message` |
| Khách vừa từ chối | AI chỉ trả về thẻ | `AI reply empty after stripping tags` |
| Nhà cung cấp AI lỗi (9Router tắt, hết quota) | Không có nội dung | `All providers failed`, `returned an empty reply` |
| Nick vừa bị PeerFlood rồi đổi proxy | Mất bộ lắng nghe (bản cũ) | `re-attached N event handler(s)` phải xuất hiện |
| Nick đang nghỉ FloodWait / tạm dừng | Không được gửi | `flood_state`, `tạm dừng` |
| Người trong blacklist hoặc là nick nội bộ | Chặn chủ động | `blacklist`, `internal managed account` |

Cách làm:
1. Đọc ảnh/chat: ai nhắn cuối, có tin admin gõ tay không, khách có từ chối không, cách nhau bao lâu.
2. Nêu 2-4 nguyên nhân có khả năng nhất, kèm cách người dùng tự kiểm tra (trạng thái chat trên dashboard, hoặc lệnh `grep` log theo user id).
3. Kiểm tra server có đang chạy bản mới không: nếu log còn hiện lỗi đã sửa trên `main` thì người dùng chưa `git pull` và khởi động lại.
4. Chỉ sửa code khi đã chỉ ra được dòng gây lỗi. Không đoán.

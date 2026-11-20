# Nghiên cứu: EA "bào lot" (volume farming lấy rebate) trên CMC Markets

> Nghiên cứu thông tin công khai (11/2026). Không phải lời khuyên đầu tư. Số liệu rebate thay đổi theo pháp nhân/khu vực của CMC: kiểm tra lại bản Rebate Schedule và Terms of Business áp dụng cho tài khoản của bạn.

## TL;DR
- **Với CMC, bào lot gần như chắc chắn lỗ.** Rebate FX của CMC tính theo **USD trên mỗi triệu notional** (khoảng 5–11 USD/triệu, khu vực SG), tức chỉ **~0,5–1,1 USD/lot chuẩn mỗi chiều**, trong khi spread EURUSD ~0,7 pip đã tốn **~7 USD/lot**. Rebate chỉ bù khoảng 15–30% chi phí spread.
- Rebate cấp IB/cashback bên thứ ba có thể cao hơn, nhưng mô hình "chỉ lấy volume" vẫn **có kỳ vọng âm** nếu chiến lược gốc không có lợi thế (edge) sau chi phí.
- Điều khoản CMC: rebate **phụ thuộc vào việc tuân thủ Rebate Terms + Terms of Business, và CMC có quyền thu hồi rebate đã trả**. Volume được gộp theo email nên mở nhiều tài khoản không lách được tier. Một số loại tài khoản (FX Active, Spectre) bị loại khỏi rebate FX.

## 1. Rebate CMC thực tế

| Khu vực | Cơ chế FX | Ngưỡng tối thiểu |
|---|---|---|
| Singapore (USD) | 5 USD/triệu (tier 20–50M) → 11 USD/triệu (≥300M) | 20M USD notional/tháng |
| UK (GBP) | ví dụ tier 2 = £7/triệu | £50M/tháng |
| Canada (CAD) | % spread Level 1: 5% (<100M) → 20% (≥300M) | 12,5M |
| Ireland (EUR) | chỉ dành cho Professional Client cư trú tại Ireland | — |

**Tính nhanh (EURUSD, 1 lot = 100.000 = 0,1 triệu notional):**
- Một vòng mở+đóng = 0,2 triệu notional → rebate ≈ **1,0–2,2 USD/lot round-trip**.
- Chi phí spread 0,7 pip ≈ **7 USD/lot round-trip** (chưa tính swap qua đêm, trượt giá).
- **Net ≈ −5 đến −6 USD/lot** trước khi tính lãi/lỗ của chiến lược. Để chạm tier cao nhất (300M/tháng ≈ 1.500 lot round-trip) bạn tốn khoảng 10.500 USD spread để nhận khoảng 3.300 USD rebate.
- Canada: rebate tối đa 20% spread → luôn mất ≥80% spread.

## 2. Các kiểu thuật toán phổ biến (từ MQL5, ForexFactory, Gumroad)

| Kiểu | Logic vào/ra | Kỳ vọng sau chi phí |
|---|---|---|
| **Hedge 2 chiều cùng tài khoản** (Buy + Sell cùng lúc, đóng chiều thắng khi đạt TP nhỏ, lưới thích ứng theo ATR) — ví dụ "Rebate Hunter", "Lot Rebate Grid Hedging" | Mở cặp Buy/Sell, mỗi chiều có TP/SL vài pip, mở lại liên tục | **Âm**: trả 2 lần spread để có vị thế phẳng. Tổng P/L xấp xỉ −(spread) cộng rebate. |
| **Hedge chéo 2 tài khoản / 2 sàn** (A Buy, B Sell) | Đóng đồng thời; một bên lời, một bên lỗ | **Âm** (2× spread) và **vi phạm ToS rõ nhất** (wash trading). Rủi ro trượt giá/requote lệch giữa 2 sàn. |
| **Grid** | Đặt lệnh theo các mức giá cách đều, chốt khi giá hồi | Ra nhiều volume, nhưng thị trường đi 1 chiều → drawdown lớn. Kỳ vọng ≈ −chi phí. |
| **Martingale / grid + gấp lot** | Thua thì gấp lot để hồi vốn | Thắng nhỏ đều đặn, **cháy tài khoản theo xác suất**. Kỳ vọng âm, rủi ro đuôi rất lớn. |
| **Scalping biên nhỏ (1–3 pip)** | Vào/ra nhanh theo tick, giữ vài giây–phút | Spread 0,7 pip ăn phần lớn TP. Rebate thường không tính cho lệnh giữ dưới 1–2 phút (churning). **Âm** nếu không có edge thật. |

Lưu ý: số liệu kiểu "3.500 lot → 35.000 USD rebate" trên trang bán EA là **quảng cáo**, giả định rebate ~10 USD/lot từ IB, chưa trừ spread và lỗ lệnh. Không có kiểm toán độc lập.

## 3. Rủi ro

1. **ToS/thu hồi**: CMC có quyền thu hồi rebate và điều kiện hóa rebate theo Terms of Business. Các hệ thống chống gian lận IB gắn cờ việc mở long/short cùng mã hoặc mã tương quan trong khoảng thời gian ngắn, và IB có rebate vượt xa lợi nhuận khách (track360). Hệ quả thường gặp: hủy rebate, đóng tài khoản, cắt IB.
2. **Tài chính**: kỳ vọng âm có hệ thống (mục 1). Grid/martingale dễ cháy tài khoản. Swap qua đêm làm lệch hedge.
3. **Pháp lý**: wash trading giữa các tài khoản liên kết có thể bị coi là thao túng/lạm dụng. Cần đọc điều khoản của từng pháp nhân CMC.
4. **Lừa đảo EA**: phần lớn EA "rebate" bán kèm link IB, nên người bán ăn hoa hồng từ chính volume lỗ của bạn.

## 4. Kết luận
Không có thuật toán "bào lot" nào có kỳ vọng dương chỉ nhờ rebate CMC: rebate chỉ là một khoản giảm phí. Nó chỉ có ích khi chiến lược **đã có lãi sau spread**. Mọi biến thể hedge (cùng tài khoản hay chéo tài khoản) đều mất tối thiểu 1 spread mỗi vòng và có rủi ro ToS cao nhất.

## Nguồn
- CMC rebates SG: https://www.cmcmarkets.com/en-sg/rebates
- CMC Rebate Schedule 11/2024: https://assets.cmcmarkets.com/pdfs/CMCMarkets-RebatesSchedule1.11.2024.pdf
- CMC Rebate Schedule 01/12/2025: https://assets.cmcmarkets.com/pdfs/CMCMarkets-RebatesSchedule01.12.2025.pdf
- CMC UK CFD rebates: https://cmcmarkets.com/en-gb/cfds-rebates
- CMC IE CFD rebates: https://cmcmarkets.com/en-ie/cfds-rebates
- CMC Canada rebate schedule: https://assets.cmcmarkets.com/legal-documents/en-ca/CMC_CFD_Rebate_Schedule.pdf
- CMC Canada rebate T&C: https://assets.cmcmarkets.com/pdfs/ca-final-monthlytradingrebatetermsandconditions.pdf
- CMC AU rebate T&C: https://assets.cmcmarkets.com/pdfs/aucfdtradingrebatetermsandconditionsmay16.pdf
- Cách tính rebate (CMC): https://www.cmcmarkets.com/en-sg/learn-cfd-trading/calculating-cash-rebates
- Track360, rebate abuse/volume manipulation: https://track360.io/learn/forex-ib-fraud-prevention/rebate-abuse-volume-manipulation
- MQL5 Rebate Hunter MT5: https://www.mql5.com/en/market/product/93706
- MQL5 Lot Rebate Grid Hedging: https://mql5.com/it/market/product/102749
- MQL5 Queen QTS Rebate Hunter (bình luận về churning): https://www.mql5.com/en/market/product/160763/comments
- MQL5 forum, rebate trading: https://www.mql5.com/en/forum/439852
- ForexFactory: https://www.forexfactory.com/thread/post/5804560 , https://www.forexfactory.com/thread/post/8293833
- EnergyExch (EA hedge cho cashback): https://www.energyexch.com/thread/post/7445125
- BabyPips "Special IB EA": https://forums.babypips.com/t/special-ib-ea/72293

Ghi chú phạm vi: không cào X/Threads (đăng nhập và ToS chặn scrape). Báo cáo chỉ dựa trên web search công khai. Chưa đọc toàn văn Terms of Business của CMC.

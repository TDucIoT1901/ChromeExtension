# Pocket Notes (Chrome MV3 Extension)

Tiện ích mở rộng ghi chú nhanh và lưu trữ liên kết tab cho Google Chrome (Manifest V3), giao diện tiếng Việt tinh gọn với phong cách màu xanh lá đậm (dark green) trên nền sáng.

## Tính năng chính
- **Ghi chú & Quản lý**: Tạo, chỉnh sửa, xóa ghi chú nhanh chóng.
- **Lưu tab hiện tại**: Tự động điền tiêu đề và liên kết của tab đang hoạt động khi bấm nút `+ Tab hiện tại`.
- **Tìm kiếm tức thì**: Lọc danh sách ghi chú theo tiêu đề, nội dung hoặc URL không phân biệt chữ hoa/thường.
- **Bảo mật & Chuẩn hóa URL**: Chỉ cho phép mở các liên kết thuộc giao thức an toàn `http` hoặc `https`. Ngăn chặn `javascript:`, `data:`, `file:` và XSS.
- **Lưu trữ cục bộ**: Sử dụng `chrome.storage.local` với thông báo lỗi trực quan trên giao diện khi gặp sự cố bộ nhớ.
- **Giao diện tối ưu**: Chiều rộng cố định 390px, tương thích chuẩn popup Chrome, tối ưu độ tương phản và trạng thái focus.

## Cấu trúc thư mục
```text
├── extension/
│   ├── manifest.json   # Cấu hình Manifest V3 (quyền storage & activeTab)
│   ├── popup.html      # Giao diện popup (HTML ngữ nghĩa, không dùng inline script)
│   ├── popup.css       # Định dạng giao diện 390px phong cách dark green
│   ├── core.js         # Module logic thuần túy (hỗ trợ cả Browser và Node CommonJS)
│   ├── popup.js        # Giao diện gửi yêu cầu tới dịch vụ nền
│   └── background.js   # Xếp hàng thao tác, bảo vệ ghi chú khi mở nhiều popup
├── tests/
│   ├── core.test.cjs   # Logic thuần
│   ├── popup.test.cjs  # Giao diện và xử lý lỗi qua API mô phỏng
│   └── background.test.cjs # Ghi đồng thời và phát hiện xung đột
└── README.md
```

## Hướng dẫn cài đặt trên Chrome
1. Mở Chrome và điều hướng đến `chrome://extensions/`.
2. Bật công tắc **Chế độ dành cho nhà phát triển** (Developer mode) ở góc trên bên phải.
3. Nhấn nút **Tải tiện ích đã giải nén** (Load unpacked).
4. Chọn thư mục `extension/` của dự án.

## Hướng dẫn chạy kiểm thử
Bộ kiểm thử sử dụng test runner tích hợp sẵn của Node.js (`node:test`), không cần cài đặt thêm bất kỳ thư viện ngoài nào:
```bash
node --test tests/*.cjs
python3 -m unittest discover -s cloud -p 'test*.py'
```

Kiểm thử gồm logic thuần và mô phỏng API Chrome: lỗi lưu, bấm lưu liên tiếp,
tải dữ liệu chậm, dữ liệu lưu bị hỏng, giới hạn số ghi chú và hiển thị văn bản.
Các kiểm thử này không thay thế việc cài và kiểm tra trực tiếp trong Chrome.

## Codespace riêng cho dự án

Mở [repository ChromeExtension](https://github.com/TDucIoT1901/ChromeExtension),
chọn **Code → Codespaces → Create codespace on main**. Cấu hình `.devcontainer`
chuẩn bị Node, Python, tmux và hai CLI từ nguồn chính thức; không khởi chạy model.
Việc đăng nhập hai tài khoản trên máy cloud do người dùng thực hiện.

Luồng phối hợp: Codex lập kế hoạch, Anti sửa trực tiếp mã nguồn trong phạm vi được
giao, bộ điều phối kiểm thử, Codex review, Anti sửa lại nếu cần. Mỗi tác vụ tối đa 20 phút,
dừng khi hoàn thành, gặp lỗi hoặc chạm ngân sách. Xem [hướng dẫn cloud](cloud/README.md).
Máy cloud độc lập với máy cá nhân nhưng vẫn chịu thời gian tự dừng của Codespaces.

GitHub Actions chạy kiểm thử Node/Python và kiểm tra khóa tiến trình thật trên Linux.
Workflow này không gọi model; việc đăng nhập và quyền ghi của hai CLI trên Codespace
cần kiểm chứng riêng. Xem trạng thái nghiệm thu trong cloud/IMPLEMENTATION_PLAN.md.

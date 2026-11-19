import SwiftUI

struct ViewerView: View {
    @ObservedObject var session: RemoteSession
    let onDisconnect: () -> Void

    @State private var dragStart: CGPoint?
    @State private var dragCurrent: CGPoint?

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Color.black.ignoresSafeArea()

                if let frame = session.frame {
                    let layout = fittedSize(image: frame.size, in: geo.size, safe: geo.safeAreaInsets)

                    Image(uiImage: frame)
                        .resizable()
                        .interpolation(.medium)
                        .frame(width: layout.size.width, height: layout.size.height)
                        .position(x: layout.origin.x + layout.size.width / 2,
                                  y: layout.origin.y + layout.size.height / 2)
                        .gesture(touchGesture(layout: layout))
                        .overlay(alignment: .topTrailing) {
                            statusBadge.padding(12)
                        }

                    if let start = dragStart, let current = dragCurrent {
                        Circle()
                            .fill(Color.accentColor.opacity(0.55))
                            .frame(width: 44, height: 44)
                            .position(current)
                            .allowsHitTesting(false)
                    }
                } else {
                    VStack(spacing: 14) {
                        ProgressView().scaleEffect(1.4)
                        Text("等待电脑画面…").foregroundStyle(.white.opacity(0.7))
                        Text("确认电脑端已开启远程控制，并核对配对码")
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.4))
                    }
                }
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("断开", role: .destructive, action: onDisconnect)
                }
            }
            .navigationTitle("Oryx 远程")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var statusBadge: some View {
        Text(String(format: "%.0f fps", session.framesPerSecond))
            .font(.caption2.monospacedDigit())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.black.opacity(0.55))
            .clipShape(Capsule())
            .foregroundStyle(.white.opacity(0.85))
    }

    // MARK: - 坐标换算

    /// 计算图片在视图内的实际绘制区域（aspect fit）。
    private func fittedSize(image: CGSize, in container: CGSize, safe: EdgeInsets) -> CGRect {
        let available = CGSize(width: container.width, height: container.height)
        guard image.width > 0, image.height > 0 else {
            return CGRect(origin: .zero, size: available)
        }
        let scale = min(available.width / image.width, available.height / image.height)
        let size = CGSize(width: image.width * scale, height: image.height * scale)
        let origin = CGPoint(x: (available.width - size.width) / 2,
                             y: (available.height - size.height) / 2)
        return CGRect(origin: origin, size: size)
    }

    private func normalized(_ point: CGPoint, in layout: CGRect) -> (Double, Double) {
        let x = (point.x - layout.minX) / max(layout.width, 1)
        let y = (point.y - layout.minY) / max(layout.height, 1)
        return (min(max(x, 0), 1), min(max(y, 0), 1))
    }

    // MARK: - 手势

    private func touchGesture(layout: CGRect) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if dragStart == nil { dragStart = value.startLocation }
                dragCurrent = value.location
            }
            .onEnded { value in
                defer {
                    dragStart = nil
                    dragCurrent = nil
                }
                let distance = hypot(value.translation.width, value.translation.height)
                let (sx, sy) = normalized(value.startLocation, in: layout)

                if distance < 12 {
                    session.sendTap(x: sx, y: sy)
                } else {
                    let (ex, ey) = normalized(value.location, in: layout)
                    session.sendSwipe(x1: sx, y1: sy, x2: ex, y2: ey)
                }
            }
    }
}

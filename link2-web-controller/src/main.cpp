#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <uvc_camera.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr int kPort = 8765;
constexpr int kUnitsPerDegree = 3600;
std::atomic<bool> g_running{true};

std::string JsonEscape(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char c : value) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) {
                out << "\\u00";
                constexpr char hex[] = "0123456789abcdef";
                out << hex[(c >> 4) & 0x0f] << hex[c & 0x0f];
            } else {
                out << static_cast<char>(c);
            }
        }
    }
    return out.str();
}

std::string UrlDecode(const std::string& input) {
    std::string output;
    output.reserve(input.size());
    for (size_t i = 0; i < input.size(); ++i) {
        if (input[i] == '%' && i + 2 < input.size()) {
            const auto hex = input.substr(i + 1, 2);
            char* end = nullptr;
            const long decoded = std::strtol(hex.c_str(), &end, 16);
            if (end && *end == '\0') {
                output.push_back(static_cast<char>(decoded));
                i += 2;
                continue;
            }
        }
        output.push_back(input[i] == '+' ? ' ' : input[i]);
    }
    return output;
}

std::map<std::string, std::string> ParseQuery(const std::string& query) {
    std::map<std::string, std::string> values;
    size_t start = 0;
    while (start <= query.size()) {
        const size_t end = query.find('&', start);
        const std::string item = query.substr(start, end == std::string::npos ? std::string::npos : end - start);
        const size_t equals = item.find('=');
        if (equals == std::string::npos) {
            values[UrlDecode(item)] = "";
        } else {
            values[UrlDecode(item.substr(0, equals))] = UrlDecode(item.substr(equals + 1));
        }
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return values;
}

int ParseInt(const std::map<std::string, std::string>& query, const std::string& key, int fallback) {
    const auto it = query.find(key);
    if (it == query.end()) return fallback;
    try {
        return std::stoi(it->second);
    } catch (...) {
        return fallback;
    }
}

bool ParseBool(const std::map<std::string, std::string>& query, const std::string& key, bool fallback) {
    const auto it = query.find(key);
    if (it == query.end()) return fallback;
    std::string value = it->second;
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    if (value == "1" || value == "true" || value == "on") return true;
    if (value == "0" || value == "false" || value == "off") return false;
    return fallback;
}

class LinkCamera {
public:
    std::string Status() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string error;
        if (!Connect(error)) {
            return ErrorJson(error);
        }

        std::string serial;
        std::string cameraType;
        int32_t pan = 0;
        int32_t tilt = 0;
        int privacyMode = 0;
        bool privacyEnabled = false;
        bool hasPosition = controller_->GetPanTiltAbsoluteValue(pan, tilt);
        // Some Link 2 firmware revisions return a legacy coordinate payload
        // outside the documented range. Do not present that value as degrees.
        hasPosition = hasPosition && pan >= -522000 && pan <= 522000 && tilt >= -162000 && tilt <= 324000;
        controller_->GetSerialNumber(serial);
        controller_->GetCameraType(cameraType);
        controller_->GetPrivacyMode(privacyMode, privacyEnabled);

        std::map<ExtendFuction, bool> features;
        controller_->GetExtendFuncStatus(features);

        const auto enabled = [&](ExtendFuction feature) {
            const auto it = features.find(feature);
            return it != features.end() && it->second;
        };

        std::ostringstream json;
        json << "{\"ok\":true"
             << ",\"connected\":true"
             << ",\"name\":\"" << JsonEscape(friendlyName_) << "\""
             << ",\"serial\":\"" << JsonEscape(serial) << "\""
             << ",\"cameraType\":\"" << JsonEscape(cameraType) << "\""
             << ",\"privacy\":" << (privacyEnabled ? "true" : "false")
             << ",\"tracking\":" << (enabled(ExtendFuction::EnableTracking) ? "true" : "false")
             << ",\"singleTapTracking\":" << (enabled(ExtendFuction::EnableSingleTapTracking) ? "true" : "false")
             << ",\"ai\":" << (enabled(ExtendFuction::Ai) ? "true" : "false");
        if (hasPosition) {
            json << ",\"panRaw\":" << pan
                 << ",\"tiltRaw\":" << tilt
                 << ",\"panDegrees\":" << (static_cast<double>(pan) / kUnitsPerDegree)
                 << ",\"tiltDegrees\":" << (static_cast<double>(tilt) / kUnitsPerDegree);
        }
        json << '}';
        return json.str();
    }

    std::string Reconnect() {
        std::lock_guard<std::mutex> lock(mutex_);
        controller_.reset();
        friendlyName_.clear();
        std::string error;
        if (!Connect(error)) return ErrorJson(error);
        return OkJson("Link 2 已重新连接");
    }

    std::string Home() {
        return WithController([](uvc::UVCCameraExtendController& camera) {
            // Link 2 physically points the lens down in privacy mode. Disable
            // tracking and privacy before centering the gimbal.
            camera.EnableExtendFuncWork(ExtendFuction::EnableTracking, false);
            camera.EnableExtendFuncWork(ExtendFuction::EnableSingleTapTracking, false);
            camera.SetPrivacyMode(false);
            Sleep(350);
            return camera.SetPanTiltAbsolute(0, 0);
        }, "已退出隐私模式，云台已回正到 0°, 0°");
    }

    std::string RecoverDownwardPose() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string error;
        if (!Connect(error)) return ErrorJson(error);

        controller_->EnableExtendFuncWork(ExtendFuction::EnableTracking, false);
        controller_->EnableExtendFuncWork(ExtendFuction::EnableSingleTapTracking, false);
        controller_->SetPrivacyMode(false);

        // SwitchNormalMode intentionally causes USB re-enumeration. The SDK
        // can report false because the device disappears before the response
        // is delivered, so reconnecting is the source of truth.
        controller_->SwitchNormalMode();
        controller_.reset();
        friendlyName_.clear();

        bool reconnected = false;
        for (int attempt = 0; attempt < 8; ++attempt) {
            Sleep(1500);
            if (Connect(error)) {
                reconnected = true;
                break;
            }
        }
        if (!reconnected) {
            return ErrorJson("已请求切回普通模式，但设备尚未重新连接；请稍后点击“重新连接 SDK”");
        }
        controller_->SetPrivacyMode(false);
        return OkJson("摄像头已切回普通模式并退出隐私姿态");
    }

    std::string Privacy(bool enabled) {
        return WithController([=](uvc::UVCCameraExtendController& camera) {
            return camera.SetPrivacyMode(enabled);
        }, enabled ? "隐私模式已开启" : "隐私模式已关闭");
    }

    std::string Move(const std::string& direction, int speed) {
        speed = std::clamp(speed, 1, 10);
        return WithController([&](uvc::UVCCameraExtendController& camera) {
            CameraControlRelativeInfo pan{};
            CameraControlRelativeInfo tilt{};
            pan.value = CameraControlRelativeValue::Stop;
            tilt.value = CameraControlRelativeValue::Stop;
            pan.speed = speed;
            tilt.speed = speed;

            if (direction == "left") {
                pan.value = CameraControlRelativeValue::AntiClockwiseMove;
            } else if (direction == "right") {
                pan.value = CameraControlRelativeValue::ClockwiseMove;
            } else if (direction == "up") {
                tilt.value = CameraControlRelativeValue::ClockwiseMove;
            } else if (direction == "down") {
                tilt.value = CameraControlRelativeValue::AntiClockwiseMove;
            } else if (direction != "stop") {
                return false;
            }
            return camera.SetPanTiltRelative(pan, tilt);
        }, direction == "stop" ? "云台已停止" : "云台开始移动");
    }

    std::string Tracking(bool enabled) {
        return WithController([=](uvc::UVCCameraExtendController& camera) {
            const bool tracking = camera.EnableExtendFuncWork(ExtendFuction::EnableTracking, enabled);
            const bool tap = camera.EnableExtendFuncWork(ExtendFuction::EnableSingleTapTracking, enabled);
            if (enabled) {
                camera.SetTrackSpeed(TrackSpeed::Normal);
                camera.SetCompositionStyle(CompositionStyle::HalfBody);
            }
            return tracking && tap;
        }, enabled ? "AI 跟踪已开启" : "AI 跟踪已关闭");
    }

    std::string AutoFraming(bool enabled) {
        return WithController([=](uvc::UVCCameraExtendController& camera) {
            if (!enabled) return camera.SwitchNormalMode();
            VideoModeAuxiliaryData data{};
            return camera.SetVideoMode(VideoMode::AutoComposition, data);
        }, enabled ? "自动构图已开启" : "已切回普通模式，视频设备可能会重新连接");
    }

private:
    template <typename Operation>
    std::string WithController(Operation operation, const std::string& message) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string error;
        if (!Connect(error)) return ErrorJson(error);
        if (!operation(*controller_)) {
            controller_.reset();
            return ErrorJson("SDK 命令失败；设备可能正被占用或刚刚重新连接");
        }
        return OkJson(message);
    }

    bool Connect(std::string& error) {
        if (controller_) return true;

        std::vector<UVCCameraInfo> devices;
        uvc::GetUVCCameraList(devices);
        if (devices.empty()) {
            error = "未发现 Insta360 Link 设备，请检查 USB 连接并关闭可能独占摄像头的软件";
            return false;
        }

        const auto it = std::find_if(devices.begin(), devices.end(), [](const UVCCameraInfo& device) {
            return device.friendly_name.find("Insta360 Link") != std::string::npos;
        });
        const UVCCameraInfo& selected = it == devices.end() ? devices.front() : *it;
        friendlyName_ = selected.friendly_name;
        controller_ = std::make_unique<uvc::UVCCameraExtendController>(selected);
        return true;
    }

    static std::string OkJson(const std::string& message) {
        return "{\"ok\":true,\"message\":\"" + JsonEscape(message) + "\"}";
    }

    static std::string ErrorJson(const std::string& message) {
        return "{\"ok\":false,\"connected\":false,\"error\":\"" + JsonEscape(message) + "\"}";
    }

    std::mutex mutex_;
    std::unique_ptr<uvc::UVCCameraExtendController> controller_;
    std::string friendlyName_;
};

struct HttpResponse {
    int status = 200;
    std::string statusText = "OK";
    std::string contentType = "application/json; charset=utf-8";
    std::string body;
};

fs::path ExecutableDirectory() {
    std::vector<wchar_t> buffer(32768);
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) return fs::current_path();
    return fs::path(std::wstring(buffer.data(), length)).parent_path();
}

std::string ReadFile(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) return {};
    return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}

HttpResponse Route(const std::string& method, const std::string& target, LinkCamera& camera) {
    const size_t queryStart = target.find('?');
    const std::string path = target.substr(0, queryStart);
    const auto query = ParseQuery(queryStart == std::string::npos ? "" : target.substr(queryStart + 1));

    if (method == "GET" && path == "/") {
        const std::string page = ReadFile(ExecutableDirectory() / "web" / "index.html");
        if (page.empty()) return {500, "Internal Server Error", "text/plain; charset=utf-8", "web/index.html not found"};
        return {200, "OK", "text/html; charset=utf-8", page};
    }
    if (method == "GET" && path == "/api/status") {
        return {200, "OK", "application/json; charset=utf-8", camera.Status()};
    }
    if (method == "POST" && path == "/api/reconnect") {
        return {200, "OK", "application/json; charset=utf-8", camera.Reconnect()};
    }
    if (method == "POST" && path == "/api/gimbal/home") {
        return {200, "OK", "application/json; charset=utf-8", camera.Home()};
    }
    if (method == "POST" && path == "/api/recover-downward-pose") {
        return {200, "OK", "application/json; charset=utf-8", camera.RecoverDownwardPose()};
    }
    if (method == "POST" && path == "/api/privacy") {
        return {200, "OK", "application/json; charset=utf-8", camera.Privacy(ParseBool(query, "enabled", false))};
    }
    if (method == "POST" && path == "/api/gimbal/move") {
        const auto it = query.find("direction");
        const std::string direction = it == query.end() ? "stop" : it->second;
        return {200, "OK", "application/json; charset=utf-8", camera.Move(direction, ParseInt(query, "speed", 3))};
    }
    if (method == "POST" && path == "/api/tracking") {
        return {200, "OK", "application/json; charset=utf-8", camera.Tracking(ParseBool(query, "enabled", true))};
    }
    if (method == "POST" && path == "/api/auto-framing") {
        return {200, "OK", "application/json; charset=utf-8", camera.AutoFraming(ParseBool(query, "enabled", true))};
    }
    if (path == "/favicon.ico") return {204, "No Content", "text/plain", ""};
    return {404, "Not Found", "application/json; charset=utf-8", "{\"ok\":false,\"error\":\"接口不存在\"}"};
}

void SendAll(SOCKET client, const std::string& data) {
    size_t sent = 0;
    while (sent < data.size()) {
        const int chunk = send(client, data.data() + sent, static_cast<int>(data.size() - sent), 0);
        if (chunk == SOCKET_ERROR || chunk == 0) break;
        sent += static_cast<size_t>(chunk);
    }
}

void HandleClient(SOCKET client, LinkCamera& camera) {
    std::string request;
    std::vector<char> buffer(8192);
    while (request.find("\r\n\r\n") == std::string::npos && request.size() < 65536) {
        const int received = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
        if (received <= 0) return;
        request.append(buffer.data(), static_cast<size_t>(received));
    }

    const size_t firstLineEnd = request.find("\r\n");
    std::istringstream firstLine(request.substr(0, firstLineEnd));
    std::string method;
    std::string target;
    std::string version;
    firstLine >> method >> target >> version;

    const HttpResponse response = Route(method, target, camera);
    std::ostringstream headers;
    headers << "HTTP/1.1 " << response.status << ' ' << response.statusText << "\r\n"
            << "Content-Type: " << response.contentType << "\r\n"
            << "Content-Length: " << response.body.size() << "\r\n"
            << "Cache-Control: no-store\r\n"
            << "Connection: close\r\n\r\n";
    SendAll(client, headers.str() + response.body);
}

void SignalHandler(int) {
    g_running = false;
}

} // namespace

int main() {
    SetConsoleOutputCP(CP_UTF8);
    std::signal(SIGINT, SignalHandler);

    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
        std::cerr << "Winsock 初始化失败\n";
        return 1;
    }

    const SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server == INVALID_SOCKET) {
        std::cerr << "创建服务器套接字失败\n";
        WSACleanup();
        return 1;
    }

    BOOL reuse = TRUE;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(kPort);

    if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        listen(server, SOMAXCONN) == SOCKET_ERROR) {
        std::cerr << "无法监听 http://127.0.0.1:" << kPort << "，端口可能已被占用\n";
        closesocket(server);
        WSACleanup();
        return 1;
    }

    LinkCamera camera;
    std::cout << "Link 2 Web Controller 已启动\n"
              << "请打开: http://127.0.0.1:" << kPort << "\n"
              << "按 Ctrl+C 退出\n";

    while (g_running) {
        const SOCKET client = accept(server, nullptr, nullptr);
        if (client == INVALID_SOCKET) {
            if (!g_running) break;
            continue;
        }
        HandleClient(client, camera);
        shutdown(client, SD_BOTH);
        closesocket(client);
    }

    closesocket(server);
    WSACleanup();
    return 0;
}

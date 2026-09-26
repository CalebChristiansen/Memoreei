import Foundation

/// "Update available…": checks the project's latest GitHub release once a day.
///
/// It only ever tells: while the app is unsigned, every update needs Full Disk Access
/// granted again, so updating someone automatically would break iMessage behind
/// their back.
final class Updates {
    struct Release { let version: String; let page: URL }

    private(set) var available: Release?
    var onChange: (() -> Void)?
    private var timer: Timer?

    func start() {
        check()
        timer = Timer.scheduledTimer(withTimeInterval: 24 * 3600, repeats: true) { [weak self] _ in
            self?.check()
        }
    }

    func check() {
        guard let api = AppInfo.releasesAPI, let url = URL(string: api) else { return }
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("Memoreei/\(AppInfo.version)", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = json["tag_name"] as? String,
                  let page = (json["html_url"] as? String).flatMap(URL.init(string:))
            else { return }
            let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.available = Version.isNewer(version, than: AppInfo.version)
                    ? Release(version: version, page: page) : nil
                self.onChange?()
            }
        }.resume()
    }
}

/// Just enough of PEP 440 for memoreei's own versions: 0.3.0, 0.3.0rc2, 0.3.1a1.
enum Version {
    static func key(_ v: String) -> [Int] {
        let release = v.prefix { $0.isNumber || $0 == "." }
        var numbers = release.split(separator: ".").compactMap { Int($0) }
        while numbers.count < 3 { numbers.append(0) }
        let rest = v.dropFirst(release.count)
        var pre = [Int.max, 0]  // a final release sorts after its pre-releases
        for (tag, rank) in [("rc", 3), ("b", 2), ("a", 1)] where rest.hasPrefix(tag) {
            pre = [rank, Int(rest.dropFirst(tag.count)) ?? 0]
            break
        }
        return numbers + pre
    }

    static func isNewer(_ a: String, than b: String) -> Bool {
        key(b).lexicographicallyPrecedes(key(a))
    }
}

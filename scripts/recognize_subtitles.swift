import Foundation
import Vision
import ImageIO

let directory = URL(fileURLWithPath: CommandLine.arguments[1])
let start = Int(CommandLine.arguments[2])!
let end = Int(CommandLine.arguments[3])!
let region = CommandLine.arguments.count > 4 ? CommandLine.arguments[4].split(separator:",").compactMap{ Double($0) } : []
final class Results: @unchecked Sendable {
    let lock = NSLock()
    var data = [Int: [[String: Any]]]()
    func put(_ index: Int, _ observations: [[String: Any]]) {
        lock.lock(); defer { lock.unlock() }
        data[index] = observations
    }
}
let results = Results()
let queue = OperationQueue()
queue.maxConcurrentOperationCount = 4
for i in start...end {
    queue.addOperation {
        let url = directory.appendingPathComponent(String(format:"%04d.jpg", i))
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        if region.count == 4 {
            request.regionOfInterest = CGRect(x: region[0], y: region[1], width: region[2], height: region[3])
        }
        do {
            try VNImageRequestHandler(url: url).perform([request])
            results.put(i, (request.results ?? []).compactMap { observation in
                guard let t = observation.topCandidates(1).first else { return nil }
                let b = observation.boundingBox
                return ["text": t.string, "confidence": t.confidence, "x": b.minX, "y": b.minY, "w": b.width, "h": b.height]
            })
        } catch {
            FileHandle.standardError.write(Data("\(i): \(error)\n".utf8))
        }
    }
}
queue.waitUntilAllOperationsAreFinished()
guard results.data.count == end - start + 1 else { exit(1) }
for i in start...end {
    let result = try JSONSerialization.data(withJSONObject: ["i":i, "v":results.data[i]!], options: [.sortedKeys])
    print(String(decoding: result, as: UTF8.self))
}

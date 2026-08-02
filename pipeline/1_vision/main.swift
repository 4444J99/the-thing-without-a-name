// danse — Vision pass.
//
// One dependency-free binary over the exported corpus. For every photograph it emits
// the two artifacts the engine's crop vocabulary is derived from:
//
//   <out>/pose/<id>.json   19 body-pose joints, normalized to a TOP-LEFT origin
//   <out>/mask/<id>.png    8-bit person-segmentation matte
//
// Nothing here draws anything. The pose model is a measuring instrument: it locates a
// shoulder, it does not invent one. That distinction is load-bearing for the work's
// provenance claim, so this stage stays strictly observational.
//
// Build: ./build.sh    Run: ./danse-vision <raw-dir> <out-dir>

import AppKit
import CoreImage
import Foundation
import Vision

// MARK: - Joint vocabulary

/// The 19 joints `VNDetectHumanBodyPoseRequest` reports, in a stable emit order.
/// Names are snake_case to match `danse.corpus.v1`; the engine keys regions off these.
let JOINTS: [(VNHumanBodyPoseObservation.JointName, String)] = [
    (.nose, "nose"),
    (.leftEye, "left_eye"), (.rightEye, "right_eye"),
    (.leftEar, "left_ear"), (.rightEar, "right_ear"),
    (.neck, "neck"),
    (.leftShoulder, "left_shoulder"), (.rightShoulder, "right_shoulder"),
    (.leftElbow, "left_elbow"), (.rightElbow, "right_elbow"),
    (.leftWrist, "left_wrist"), (.rightWrist, "right_wrist"),
    (.root, "root"),
    (.leftHip, "left_hip"), (.rightHip, "right_hip"),
    (.leftKnee, "left_knee"), (.rightKnee, "right_knee"),
    (.leftAnkle, "left_ankle"), (.rightAnkle, "right_ankle"),
]

/// Classification thresholds — set from what the corpus actually is.
///
/// Measured over all 162 frames: segmentation succeeds on 161 (coverage 11–18%,
/// quality 0.987–0.998), while pose returns joints on only 65 and *never* reaches 8
/// confident joints. The joint histogram says why — knees 40%, ankles 37%, hips 35%,
/// then shoulders 3% and faces 2%. The shoot frames legs; there is no upper body for
/// a whole-person pose model to anchor on.
///
/// So the matte is the primary instrument and pose is an optional refinement. Gating
/// on pose would have discarded 60% of a corpus in which the subject is unmistakably
/// present. `figure` and `dancer` are both the body stratum; `dancer` merely carries
/// named joints on top of the silhouette.
let CONF_STRONG: Float = 0.50      // a joint at/above this is trustworthy for naming
let POSE_USEFUL_JOINTS = 4         // enough named joints to label anatomy at all
let COVERAGE_FLOOR: Double = 0.05  // matte below 5% of frame ⇒ architecture, no subject

// MARK: - Image loading

struct LoadedImage {
    let cgImage: CGImage
    let orientation: CGImagePropertyOrientation
    /// Dimensions *after* orientation is applied — the space Vision reports in.
    let width: Int
    let height: Int
}

/// EXIF orientation matters here: these are 2017 iPhone frames, and pose coordinates
/// are reported relative to the *oriented* image. Reading it wrong silently rotates
/// every crop rect in the corpus.
func loadImage(_ url: URL) -> LoadedImage? {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(src, 0, nil)
    else { return nil }

    var orientation: CGImagePropertyOrientation = .up
    if let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
       let raw = props[kCGImagePropertyOrientation] as? UInt32,
       let parsed = CGImagePropertyOrientation(rawValue: raw) {
        orientation = parsed
    }

    // Orientations 5–8 transpose the axes.
    let swaps: Set<CGImagePropertyOrientation> = [.leftMirrored, .right, .rightMirrored, .left]
    let w = swaps.contains(orientation) ? cg.height : cg.width
    let h = swaps.contains(orientation) ? cg.width : cg.height

    return LoadedImage(cgImage: cg, orientation: orientation, width: w, height: h)
}

// MARK: - Mask writing

let ciContext = CIContext(options: [.useSoftwareRenderer: false])

struct MaskResult {
    let width: Int
    let height: Int
    let coverage: Double
    /// Mean matte value across covered pixels — a cheap proxy for edge confidence.
    /// A crisp matte sits near 1.0; a frayed one drifts down, and the manifest uses
    /// this to gate bad mattes out of the region vocabulary.
    let quality: Double
}

/// Writes the segmentation buffer as an 8-bit grayscale PNG and measures it.
func writeMask(_ buffer: CVPixelBuffer, to url: URL) -> MaskResult? {
    let w = CVPixelBufferGetWidth(buffer)
    let h = CVPixelBufferGetHeight(buffer)

    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }

    guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    let bytes = base.assumingMemoryBound(to: UInt8.self)

    var covered = 0
    var sum = 0.0
    for y in 0..<h {
        let row = bytes + y * stride
        for x in 0..<w {
            let v = row[x]
            if v > 127 { covered += 1; sum += Double(v) / 255.0 }
        }
    }
    let total = Double(w * h)
    let coverage = Double(covered) / total
    let quality = covered > 0 ? sum / Double(covered) : 0.0

    // CIImage from a OneComponent8 buffer renders as grayscale; PNG-encode it directly.
    let ci = CIImage(cvPixelBuffer: buffer)
    guard let space = CGColorSpace(name: CGColorSpace.linearGray),
          let png = ciContext.pngRepresentation(
              of: ci, format: .L8, colorSpace: space, options: [:])
    else { return nil }

    do { try png.write(to: url) } catch { return nil }
    return MaskResult(width: w, height: h, coverage: coverage, quality: quality)
}

// MARK: - Pose extraction

struct JointReading {
    let name: String
    let x: Double   // normalized, top-left origin
    let y: Double
    let conf: Float
}

/// Vision reports normalized points with a **bottom-left** origin. Every consumer
/// downstream (crop rects, `anchorY`, the registration homography) works in image
/// space, so the flip happens exactly once, here.
func extractPose(_ observation: VNHumanBodyPoseObservation) -> [JointReading] {
    guard let points = try? observation.recognizedPoints(.all) else { return [] }
    var out: [JointReading] = []
    for (joint, name) in JOINTS {
        guard let p = points[joint], p.confidence > 0 else { continue }
        out.append(JointReading(
            name: name,
            x: Double(p.location.x),
            y: 1.0 - Double(p.location.y),
            conf: p.confidence))
    }
    return out
}

// MARK: - JSON emit

func jsonString(_ value: Any) -> String {
    guard let data = try? JSONSerialization.data(
        withJSONObject: value, options: [.sortedKeys, .prettyPrinted]),
        let s = String(data: data, encoding: .utf8)
    else { return "{}" }
    return s
}

// MARK: - Driver

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write(
        "usage: danse-vision <raw-dir> <out-dir>\n".data(using: .utf8)!)
    exit(2)
}

let rawDir = URL(fileURLWithPath: args[1], isDirectory: true)
let outDir = URL(fileURLWithPath: args[2], isDirectory: true)
let poseDir = outDir.appendingPathComponent("pose")
let maskDir = outDir.appendingPathComponent("mask")
let indexURL = outDir.appendingPathComponent("vision.json")

let fm = FileManager.default
for dir in [poseDir, maskDir] {
    try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
}

let exts: Set<String> = ["jpg", "jpeg", "png", "heic", "tif", "tiff"]
guard let entries = try? fm.contentsOfDirectory(
    at: rawDir, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles])
else {
    FileHandle.standardError.write("cannot read \(rawDir.path)\n".data(using: .utf8)!)
    exit(1)
}

let images = entries
    .filter { exts.contains($0.pathExtension.lowercased()) }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }

guard !images.isEmpty else {
    FileHandle.standardError.write("no images in \(rawDir.path)\n".data(using: .utf8)!)
    exit(1)
}
try? fm.removeItem(at: indexURL)

var index: [[String: Any]] = []
var counts = ["dancer": 0, "figure": 0, "room": 0]
var failed = 0

for (i, url) in images.enumerated() {
    let id = url.deletingPathExtension().lastPathComponent
    let poseURL = poseDir.appendingPathComponent("\(id).json")
    let maskURL = maskDir.appendingPathComponent("\(id).png")

    // Never let a failed rerun inherit a prior frame's measurements. A frame is
    // complete only when this invocation emits both artifacts again.
    try? fm.removeItem(at: poseURL)
    try? fm.removeItem(at: maskURL)

    guard let img = loadImage(url) else {
        FileHandle.standardError.write("skip (unreadable): \(id)\n".data(using: .utf8)!)
        failed += 1
        continue
    }

    let handler = VNImageRequestHandler(
        cgImage: img.cgImage, orientation: img.orientation, options: [:])

    let poseRequest = VNDetectHumanBodyPoseRequest()
    let segRequest = VNGeneratePersonSegmentationRequest()
    segRequest.qualityLevel = .accurate
    segRequest.outputPixelFormat = kCVPixelFormatType_OneComponent8

    do {
        try handler.perform([poseRequest, segRequest])
    } catch {
        FileHandle.standardError.write(
            "skip (vision failed): \(id): \(error)\n".data(using: .utf8)!)
        failed += 1
        continue
    }

    // --- pose ---
    var joints: [JointReading] = []
    var poseConf: Float = 0
    if let obs = (poseRequest.results ?? []).max(by: { $0.confidence < $1.confidence }) {
        joints = extractPose(obs)
        poseConf = obs.confidence
    }
    let strongCount = joints.filter { $0.conf >= CONF_STRONG }.count

    // --- mask ---
    guard let segmentation = (segRequest.results ?? []).first,
          let mask = writeMask(segmentation.pixelBuffer, to: maskURL)
    else {
        FileHandle.standardError.write(
            "skip (segmentation missing): \(id)\n".data(using: .utf8)!)
        failed += 1
        continue
    }
    let coverage = mask.coverage

    // --- partition ---
    // Coverage decides membership; pose only decides how richly the body can be labelled.
    let cls: String
    if coverage < COVERAGE_FLOOR {
        cls = "room"
    } else if strongCount >= POSE_USEFUL_JOINTS {
        cls = "dancer"
    } else {
        cls = "figure"
    }
    var jointMap: [String: [Any]] = [:]
    for j in joints {
        jointMap[j.name] = [
            (j.x * 10000).rounded() / 10000,
            (j.y * 10000).rounded() / 10000,
            (Double(j.conf) * 1000).rounded() / 1000,
        ]
    }

    let record: [String: Any] = [
        "id": id,
        "file": url.lastPathComponent,
        "w": img.width,
        "h": img.height,
        "orientation": Int(img.orientation.rawValue),
        "class": cls,
        "pose": [
            "conf": (Double(poseConf) * 1000).rounded() / 1000,
            "strong": strongCount,
            "joints": jointMap,
        ],
        "mask": [
            "w": mask.width,
            "h": mask.height,
            "coverage": (mask.coverage * 10000).rounded() / 10000,
            "quality": (mask.quality * 1000).rounded() / 1000,
            "file": "mask/\(id).png",
        ] as [String: Any],
    ]

    do {
        try jsonString(record).write(to: poseURL, atomically: true, encoding: .utf8)
    } catch {
        try? fm.removeItem(at: maskURL)
        FileHandle.standardError.write(
            "skip (receipt write failed): \(id): \(error)\n".data(using: .utf8)!)
        failed += 1
        continue
    }
    counts[cls, default: 0] += 1
    index.append(record)

    if (i + 1) % 10 == 0 || i == images.count - 1 {
        FileHandle.standardError.write(
            "  \(i + 1)/\(images.count)\n".data(using: .utf8)!)
    }
}

let summary: [String: Any] = [
    "schema": "danse.vision.v1",
    "source": rawDir.path,
    "count": index.count,
    "classes": counts,
    "thresholds": [
        "conf_strong": CONF_STRONG,
        "pose_useful_joints": POSE_USEFUL_JOINTS,
        "coverage_floor": COVERAGE_FLOOR,
    ],
    "photos": index,
]
do {
    try jsonString(summary).write(to: indexURL, atomically: true, encoding: .utf8)
} catch {
    FileHandle.standardError.write(
        "cannot write \(indexURL.path): \(error)\n".data(using: .utf8)!)
    exit(1)
}

print("danse-vision: \(index.count) photographs")
print("  dancer  \(counts["dancer"] ?? 0)")
print("  figure  \(counts["figure"] ?? 0)")
print("  room    \(counts["room"] ?? 0)")
print("  → \(outDir.path)/vision.json")
if failed > 0 || index.count != images.count {
    FileHandle.standardError.write(
        "incomplete Vision hydration: \(index.count)/\(images.count) photographs; \(failed) failed\n"
            .data(using: .utf8)!)
    exit(1)
}

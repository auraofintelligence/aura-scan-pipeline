// RawFrameRecorder.swift
//
// Writes the generic raw depth package that the PC pipeline ingests.
// The parser is src/aura_scan/io/raw_rgbd.py and the spec is docs/formats.md;
// this file must keep matching both. The contract, in full:
//
//   <package>/
//     intrinsics.json   {"width","height","fx","fy","cx","cy"} pinhole
//                       intrinsics in pixels, stated at the depth resolution
//     poses.csv         header exactly: frame,x,y,z,qx,qy,qz,qw
//                       one row per frame, camera-to-world, position in
//                       metres, unit quaternion scalar-last (scipy order)
//     depth/00000.png   one 16-bit single-channel PNG per frame, values in
//                       millimetres, 0 = no data, filename = frame number
//     rgb/00000.jpg     optional colour per frame, matched by frame number
//     confidence/*.png  written for the record; the generic parser ignores
//                       this folder (only the Stray layout reads confidence)
//
// Frame conventions the parser relies on:
//   - World: right-handed, Z up, metres. ARKit's world is gravity-aligned
//     Y up, so every pose is pre-multiplied by the Y-up-to-Z-up rotation,
//     the same matrix as _Y_UP_TO_Z_UP in raw_rgbd.py.
//   - Camera: OpenCV pinhole, +X right, +Y down, +Z forward. ARKit cameras
//     are +X right, +Y up, +Z backward, so every pose is post-multiplied by
//     diag(1, -1, -1): a 180 degree turn about the camera X axis.
//   - Poses are camera-to-world; the PC inverts them for TSDF integration.
//
// Threading: record(frame:) runs on the AR session delegate queue. Pixel
// conversion happens there (a 256x192 depth map is small, and the JPEG encode
// at a stride of 3 is well inside budget); file writes go to a serial IO
// queue so the camera callback never blocks on disk.

import ARKit
import CoreGraphics
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
import simd

final class RawFrameRecorder {

    enum RecorderError: LocalizedError {
        case packageFolderExists(String)
        case pngEncodeFailed(String)
        case posesFileUnavailable
        case noFramesRecorded

        var errorDescription: String? {
            switch self {
            case .packageFolderExists(let path):
                return "Raw package folder already exists: \(path)"
            case .pngEncodeFailed(let name):
                return "Could not encode \(name) as PNG"
            case .posesFileUnavailable:
                return "Could not open poses.csv for writing"
            case .noFramesRecorded:
                return "No frames with scene depth were recorded"
            }
        }
    }

    /// Depth pixels below this ARKit confidence are zeroed. Zero means
    /// "no data" in the package spec, so the PC never fuses shaky depth.
    var minimumConfidence: ARConfidenceLevel = .medium

    /// Longest side of the saved rgb JPEGs. The captured image is 1920x1440;
    /// 960 keeps colour useful for TSDF texturing without bloating transfers.
    var rgbLongestSide: CGFloat = 960

    var jpegQuality: Double = 0.85

    private let packageURL: URL
    private let depthFolderURL: URL
    private let rgbFolderURL: URL
    private let confidenceFolderURL: URL
    private let writeRGB: Bool
    private let ioQueue = DispatchQueue(label: "au.auraofintelligence.aurascan.rawwriter", qos: .utility)
    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private let fileManager = FileManager.default

    // Touched only on the AR session delegate queue.
    private var nextFrameIndex = 0
    private var intrinsicsWritten = false

    // Touched only on ioQueue after init.
    private var posesHandle: FileHandle?

    // First asynchronous write failure, surfaced by finish().
    private let errorLock = NSLock()
    private var firstIOError: Error?

    /// Same rotation as _Y_UP_TO_Z_UP in raw_rgbd.py: ARKit gravity Y up
    /// becomes pipeline Z up.
    static let yUpToZUp = simd_float4x4(rows: [
        SIMD4<Float>(1, 0, 0, 0),
        SIMD4<Float>(0, 0, -1, 0),
        SIMD4<Float>(0, 1, 0, 0),
        SIMD4<Float>(0, 0, 0, 1),
    ])

    /// ARKit camera axes to OpenCV camera axes: flip Y and Z.
    static let arkitCameraToOpenCV = simd_float4x4(diagonal: SIMD4<Float>(1, -1, -1, 1))

    init(packageURL: URL, writeRGB: Bool) throws {
        self.packageURL = packageURL
        self.writeRGB = writeRGB
        depthFolderURL = packageURL.appendingPathComponent("depth", isDirectory: true)
        rgbFolderURL = packageURL.appendingPathComponent("rgb", isDirectory: true)
        confidenceFolderURL = packageURL.appendingPathComponent("confidence", isDirectory: true)

        if fileManager.fileExists(atPath: packageURL.path) {
            throw RecorderError.packageFolderExists(packageURL.lastPathComponent)
        }
        try fileManager.createDirectory(at: depthFolderURL, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: confidenceFolderURL, withIntermediateDirectories: true)
        if writeRGB {
            try fileManager.createDirectory(at: rgbFolderURL, withIntermediateDirectories: true)
        }

        let posesURL = packageURL.appendingPathComponent("poses.csv")
        guard fileManager.createFile(atPath: posesURL.path, contents: nil),
              let handle = try? FileHandle(forWritingTo: posesURL) else {
            throw RecorderError.posesFileUnavailable
        }
        posesHandle = handle
        // Header must match raw_rgbd.py's quaternion form character for
        // character (whitespace in headers is stripped, but keep it exact).
        try handle.write(contentsOf: Data("frame,x,y,z,qx,qy,qz,qw\n".utf8))
    }

    /// Record one ARFrame into the package. Returns false when the frame has
    /// no scene depth yet (the first few frames after session start).
    /// Call on the AR session delegate queue only.
    func record(frame: ARFrame) throws -> Bool {
        guard let sceneDepth = frame.sceneDepth else { return false }
        let index = nextFrameIndex

        // Depth: Float32 metres to UInt16 millimetres, low confidence zeroed.
        let converted = convertDepth(
            depthMap: sceneDepth.depthMap,
            confidenceMap: sceneDepth.confidenceMap
        )
        guard let converted else { return false }

        if !intrinsicsWritten {
            // camera.intrinsics is stated at camera.imageResolution (1920x1440
            // on the 14 Pro Max); scale to the depth resolution so
            // intrinsics.json matches the PNGs and the PC never has to guess.
            try writeIntrinsics(
                camera: frame.camera,
                depthWidth: converted.width,
                depthHeight: converted.height
            )
            intrinsicsWritten = true
        }

        let poseRow = Self.poseCSVRow(index: index, arkitCameraTransform: frame.camera.transform)

        var jpeg: Data?
        if writeRGB {
            jpeg = jpegData(from: frame.capturedImage)
        }

        nextFrameIndex += 1

        let depthURL = depthFolderURL.appendingPathComponent(String(format: "%05d.png", index))
        let confidenceURL = confidenceFolderURL.appendingPathComponent(String(format: "%05d.png", index))
        let rgbURL = rgbFolderURL.appendingPathComponent(String(format: "%05d.jpg", index))

        ioQueue.async { [self] in
            do {
                try Self.writeGrey16PNG(converted.millimetres,
                                        width: converted.width, height: converted.height,
                                        to: depthURL)
                if let confidencePixels = converted.confidence {
                    try Self.writeGrey8PNG(confidencePixels,
                                           width: converted.width, height: converted.height,
                                           to: confidenceURL)
                }
                if let jpeg {
                    try jpeg.write(to: rgbURL, options: .atomic)
                }
                // The pose row is appended only after its depth PNG lands, so
                // a mid-capture crash never leaves poses.csv pointing at a
                // missing frame. (The parser would skip it anyway.)
                try posesHandle?.write(contentsOf: Data(poseRow.utf8))
            } catch {
                errorLock.lock()
                if firstIOError == nil { firstIOError = error }
                errorLock.unlock()
            }
        }
        return true
    }

    /// Drain pending writes, close poses.csv, and surface any write failure.
    /// Call once, from any queue except ioQueue.
    func finish() throws {
        ioQueue.sync { }   // barrier: every queued frame write has completed
        try? posesHandle?.close()
        posesHandle = nil
        errorLock.lock()
        let failure = firstIOError
        errorLock.unlock()
        if let failure { throw failure }
        if nextFrameIndex == 0 { throw RecorderError.noFramesRecorded }
    }

    // MARK: - Pose

    /// One poses.csv row: pipeline world frame (Z up), OpenCV camera axes,
    /// camera-to-world, quaternion scalar-last. Newline included.
    static func poseCSVRow(index: Int, arkitCameraTransform: simd_float4x4) -> String {
        let pose = yUpToZUp * arkitCameraTransform * arkitCameraToOpenCV
        let rotation = simd_float3x3(
            SIMD3<Float>(pose.columns.0.x, pose.columns.0.y, pose.columns.0.z),
            SIMD3<Float>(pose.columns.1.x, pose.columns.1.y, pose.columns.1.z),
            SIMD3<Float>(pose.columns.2.x, pose.columns.2.y, pose.columns.2.z)
        )
        let q = simd_normalize(simd_quatf(rotation))
        let t = pose.columns.3
        return String(
            format: "%d,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
            index, t.x, t.y, t.z, q.imag.x, q.imag.y, q.imag.z, q.real
        )
    }

    // MARK: - Intrinsics

    private struct Intrinsics: Codable {
        let width: Int
        let height: Int
        let fx: Double
        let fy: Double
        let cx: Double
        let cy: Double
    }

    private func writeIntrinsics(camera: ARCamera, depthWidth: Int, depthHeight: Int) throws {
        let native = camera.intrinsics   // column-major 3x3 at imageResolution
        let scaleX = Double(depthWidth) / Double(camera.imageResolution.width)
        let scaleY = Double(depthHeight) / Double(camera.imageResolution.height)
        let intrinsics = Intrinsics(
            width: depthWidth,
            height: depthHeight,
            fx: Double(native.columns.0.x) * scaleX,
            fy: Double(native.columns.1.y) * scaleY,
            cx: Double(native.columns.2.x) * scaleX,
            cy: Double(native.columns.2.y) * scaleY
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(intrinsics)
        let url = packageURL.appendingPathComponent("intrinsics.json")
        ioQueue.async { [self] in
            do {
                try data.write(to: url, options: .atomic)
            } catch {
                errorLock.lock()
                if firstIOError == nil { firstIOError = error }
                errorLock.unlock()
            }
        }
    }

    // MARK: - Depth conversion

    private struct ConvertedDepth {
        let millimetres: [UInt16]
        let confidence: [UInt8]?
        let width: Int
        let height: Int
    }

    /// Float32 metres to UInt16 millimetres. NaN, non-positive, out-of-range
    /// and low-confidence readings all become 0, the spec's "no data" value.
    private func convertDepth(depthMap: CVPixelBuffer, confidenceMap: CVPixelBuffer?) -> ConvertedDepth? {
        guard CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32 else {
            return nil
        }
        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }

        let width = CVPixelBufferGetWidth(depthMap)
        let height = CVPixelBufferGetHeight(depthMap)
        guard let depthBase = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
        let depthRowBytes = CVPixelBufferGetBytesPerRow(depthMap)

        var confidenceBase: UnsafeMutableRawPointer?
        var confidenceRowBytes = 0
        var confidenceLocked = false
        if let confidenceMap,
           CVPixelBufferGetWidth(confidenceMap) == width,
           CVPixelBufferGetHeight(confidenceMap) == height {
            CVPixelBufferLockBaseAddress(confidenceMap, .readOnly)
            confidenceLocked = true
            confidenceBase = CVPixelBufferGetBaseAddress(confidenceMap)
            confidenceRowBytes = CVPixelBufferGetBytesPerRow(confidenceMap)
        }
        defer {
            if confidenceLocked, let confidenceMap {
                CVPixelBufferUnlockBaseAddress(confidenceMap, .readOnly)
            }
        }

        let threshold = UInt8(minimumConfidence.rawValue)
        var millimetres = [UInt16](repeating: 0, count: width * height)
        var confidenceCopy: [UInt8]? = confidenceBase != nil
            ? [UInt8](repeating: 0, count: width * height) : nil

        for row in 0..<height {
            let depthRow = (depthBase + row * depthRowBytes).assumingMemoryBound(to: Float32.self)
            let confidenceRow = confidenceBase.map {
                ($0 + row * confidenceRowBytes).assumingMemoryBound(to: UInt8.self)
            }
            for col in 0..<width {
                let out = row * width + col
                if let confidenceRow {
                    let c = confidenceRow[col]
                    confidenceCopy?[out] = c
                    if c < threshold { continue }   // leaves 0 = no data
                }
                let metres = depthRow[col]
                guard metres.isFinite, metres > 0 else { continue }
                let mm = (Double(metres) * 1000.0).rounded()
                guard mm >= 1, mm <= 65535 else { continue }
                millimetres[out] = UInt16(mm)
            }
        }
        return ConvertedDepth(millimetres: millimetres, confidence: confidenceCopy,
                              width: width, height: height)
    }

    // MARK: - RGB

    private func jpegData(from pixelBuffer: CVPixelBuffer) -> Data? {
        var image = CIImage(cvPixelBuffer: pixelBuffer)
        let longest = max(image.extent.width, image.extent.height)
        if longest > rgbLongestSide {
            let scale = rgbLongestSide / longest
            image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        guard let colourSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        let qualityKey = CIImageRepresentationOption(
            rawValue: kCGImageDestinationLossyCompressionQuality as String)
        return ciContext.jpegRepresentation(
            of: image,
            colorSpace: colourSpace,
            options: [qualityKey: jpegQuality]
        )
    }

    // MARK: - PNG writing via ImageIO (no third-party libraries)

    /// 16-bit single-channel PNG. The CGImage is built over big-endian
    /// samples because PNG is big-endian on the wire; byteOrder16Big tells
    /// CoreGraphics the buffer already matches, so values pass through
    /// unswapped. Pillow on the PC reads these back as uint16 exactly.
    static func writeGrey16PNG(_ pixels: [UInt16], width: Int, height: Int, to url: URL) throws {
        precondition(pixels.count == width * height)
        var bigEndian = pixels.map { $0.bigEndian }
        let data = bigEndian.withUnsafeMutableBufferPointer { Data(buffer: $0) }
        let bitmapInfo = CGBitmapInfo(rawValue:
            CGImageAlphaInfo.none.rawValue | CGBitmapInfo.byteOrder16Big.rawValue)
        guard let provider = CGDataProvider(data: data as CFData),
              let image = CGImage(
                width: width, height: height,
                bitsPerComponent: 16, bitsPerPixel: 16,
                bytesPerRow: width * 2,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: bitmapInfo,
                provider: provider, decode: nil,
                shouldInterpolate: false, intent: .defaultIntent)
        else { throw RecorderError.pngEncodeFailed(url.lastPathComponent) }
        try writePNG(image, to: url)
    }

    /// 8-bit single-channel PNG, used for the confidence maps (values 0 to 2).
    static func writeGrey8PNG(_ pixels: [UInt8], width: Int, height: Int, to url: URL) throws {
        precondition(pixels.count == width * height)
        let data = Data(pixels)
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue)
        guard let provider = CGDataProvider(data: data as CFData),
              let image = CGImage(
                width: width, height: height,
                bitsPerComponent: 8, bitsPerPixel: 8,
                bytesPerRow: width,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: bitmapInfo,
                provider: provider, decode: nil,
                shouldInterpolate: false, intent: .defaultIntent)
        else { throw RecorderError.pngEncodeFailed(url.lastPathComponent) }
        try writePNG(image, to: url)
    }

    private static func writePNG(_ image: CGImage, to url: URL) throws {
        guard let destination = CGImageDestinationCreateWithURL(
            url as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { throw RecorderError.pngEncodeFailed(url.lastPathComponent) }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw RecorderError.pngEncodeFailed(url.lastPathComponent)
        }
    }
}

// ScanMetadata.swift
// Field-side metadata mirroring the PC pipeline's manifest.yaml
// (src/aura_scan/project.py MANIFEST_TEMPLATE). Keys serialise in snake_case
// so the exported manifest.json can be copied straight into manifest.yaml:
// JSON is valid YAML, and the PC side tolerates missing keys.

import Foundation

/// Asset vocabulary shared with the PC pipeline and the places catalogue.
enum AssetType: String, CaseIterable, Identifiable, Codable {
    case picnicTable = "picnic_table"
    case toilet
    case bbq
    case bubbler
    case hall
    case library
    case clubAsset = "club_asset"
    case skateFeature = "skate_feature"
    case other

    var id: String { rawValue }

    var title: String {
        switch self {
        case .picnicTable: return "Picnic table"
        case .toilet: return "Toilet"
        case .bbq: return "BBQ"
        case .bubbler: return "Bubbler"
        case .hall: return "Hall"
        case .library: return "Library"
        case .clubAsset: return "Club asset"
        case .skateFeature: return "Skate feature"
        case .other: return "Other"
        }
    }
}

struct ScanMetadata: Codable {
    var name: String = ""
    var mode: CaptureMode = .general
    var assetType: AssetType = .other
    var condition: String = ""
    var notes: String = ""
    var capture = Capture()
    var location = Location()
    var links = Links()
    var permissions = Permissions()
    var control = Control()

    struct Capture: Codable {
        var app: String = "aura-scan-capture"
        var device: String = "iPhone14ProMax"
        var capturedAt: String = ""

        enum CodingKeys: String, CodingKey {
            case app, device
            case capturedAt = "captured_at"
        }
    }

    struct Location: Codable {
        var lat: Double?
        var lng: Double?
        var gpsAccuracyM: Double?
        var localFrame: String = ""
        var notes: String = ""

        enum CodingKeys: String, CodingKey {
            case lat, lng, notes
            case gpsAccuracyM = "gps_accuracy_m"
            case localFrame = "local_frame"
        }
    }

    struct Links: Codable {
        var placesLinkId: String?
        var sensoriumTile: String?
        var mindPalace: String?

        enum CodingKeys: String, CodingKey {
            case placesLinkId = "places_link_id"
            case sensoriumTile = "sensorium_tile"
            case mindPalace = "mind_palace"
        }
    }

    struct Permissions: Codable {
        var capturedBy: String = ""
        var consentNote: String = ""

        enum CodingKeys: String, CodingKey {
            case capturedBy = "captured_by"
            case consentNote = "consent_note"
        }
    }

    /// Control points marked on the phone. picked_points paste straight into
    /// manifest.yaml; the tape-measured distance_constraints are usually typed
    /// on the PC after the field visit.
    struct Control: Codable {
        var pickedPoints: [[Double]] = []
        var pickedPointNotes: [String] = []
        var distanceConstraints: [DistanceConstraint] = []

        struct DistanceConstraint: Codable {
            var points: [Int]
            var distanceM: Double
            var note: String = ""

            enum CodingKeys: String, CodingKey {
                case points, note
                case distanceM = "distance_m"
            }
        }

        enum CodingKeys: String, CodingKey {
            case pickedPoints = "picked_points"
            case pickedPointNotes = "picked_point_notes"
            case distanceConstraints = "distance_constraints"
        }
    }

    enum CodingKeys: String, CodingKey {
        case name, mode, condition, notes, capture, location, links, permissions, control
        case assetType = "asset_type"
    }

    /// Manifest-compatible JSON for the export package. Unset optionals are
    /// omitted rather than written as null; the PC side treats both the same.
    func manifestJSON() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }
}

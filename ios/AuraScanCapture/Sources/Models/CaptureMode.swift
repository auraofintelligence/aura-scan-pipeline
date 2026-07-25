// CaptureMode.swift
// Mirrors the PC pipeline's modes (configs/general.yaml, asset.yaml,
// projection_surface.yaml). Raw values match manifest.yaml `mode:` exactly.

import Foundation

enum CaptureMode: String, CaseIterable, Identifiable, Codable {
    case general
    case asset
    case projectionSurface = "projection_surface"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return "General"
        case .asset: return "Asset"
        case .projectionSurface: return "Projection surface"
        }
    }

    /// Short blurb for the mode picker.
    var summary: String {
        switch self {
        case .general:
            return "Any geometry, balanced defaults."
        case .asset:
            return "Public assets: picnic tables, toilets, BBQs, skate features. The PC adds oriented dimensions and an inventory-ready outline."
        case .projectionSurface:
            return "Flat or near-flat surfaces for light projection art. The PC adds a plane fit, planarity RMSE and a suitability verdict."
        }
    }

    /// On-screen coverage hints shown while scanning.
    var guidance: String {
        switch self {
        case .general:
            return "Move slowly in a loop around the subject with plenty of overlap between passes. Revisit thin spots from a different height to fill holes."
        case .asset:
            return "Orbit the asset twice: one pass at chest height, one low pass angled up under tables and seats. Capture where it meets the ground, then step back for one wide pass."
        case .projectionSurface:
            return "Stand square-on to the surface and sweep side to side at a steady distance. Cover all four edges plus a little of the surrounds, then finish with one diagonal pass."
        }
    }
}

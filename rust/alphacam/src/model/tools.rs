//! Cutting tools and the tool library.

use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

use crate::error::{Result, value};
use crate::i18n::tr;

pub const TOOL_TYPES: &[&str] = &["turn", "groove", "thread", "drill", "bore"];

/// A polyline of `[Z, radius]` points in mm.
pub type Polyline = Vec<[f64; 2]>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Tool {
    /// turn, groove, thread, drill or bore (written first, as the Python app does)
    #[serde(rename = "type")]
    pub kind: String,
    pub number: i32,
    pub name: String,
    pub nose_radius: f64,
    pub angle: f64,
    /// grooving tip width
    pub width: f64,
    /// R, L or N
    pub hand: String,
    pub default_feed: f64,
    /// clearance angles from the Z axis, as in ATTOOLS (tip angle = front - rear; negative = internal / boring)
    pub front_angle: f64,
    pub rear_angle: f64,
    pub description: String,
    /// m/min for the cycle time estimate; 0 = the default for the tool type
    pub surface_speed: f64,
    /// spindle limit for this tool; 0 = the machine's
    pub max_rpm: f64,
    /// the tool's picture as polylines of [Z, radius] points in mm, tip at [0, 0] (from ALPHA-T's ATOOLIB.TL2);
    /// empty = drawn from the type and the clearance angles (tool_shapes)
    pub outline: Vec<Polyline>,
}

impl Default for Tool {
    fn default() -> Self {
        Self {
            kind: "turn".into(),
            number: 1,
            name: String::new(),
            nose_radius: 0.0,
            angle: 0.0,
            width: 0.0,
            hand: "R".into(),
            default_feed: 0.15,
            front_angle: 0.0,
            rear_angle: 0.0,
            description: String::new(),
            surface_speed: 0.0,
            max_rpm: 0.0,
            outline: vec![],
        }
    }
}

impl Tool {
    pub fn validate(&self) -> Result<()> {
        if !TOOL_TYPES.contains(&self.kind.as_str()) {
            return value(tr("unknown tool type {type}", &[("type", &format!("'{}'", self.kind))]));
        }
        if !["R", "L", "N"].contains(&self.hand.as_str()) {
            return value(tr("tool hand must be R, L or N, not {hand}", &[("hand", &format!("'{}'", self.hand))]));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct ToolLibrary {
    pub tools: Vec<Tool>,
}

impl ToolLibrary {
    pub fn get(&self, number: i32) -> Option<&Tool> {
        self.tools.iter().find(|t| t.number == number)
    }

    pub fn to_json(&self) -> serde_json::Value {
        let mut tools = self.tools.clone();
        tools.sort_by_key(|t| t.number);
        serde_json::json!({"format": "alcam-tools/1", "tools": tools})
    }

    pub fn from_json(data: &serde_json::Value) -> Result<Self> {
        if data.get("format").and_then(|f| f.as_str()) != Some("alcam-tools/1") {
            let f = data.get("format").map(|f| f.to_string()).unwrap_or("None".into());
            return value(tr("unsupported tool library format {get_format}", &[("get_format", &f)]));
        }
        let tools: Vec<Tool> = serde_json::from_value(data.get("tools").cloned().unwrap_or_default())
            .map_err(|e| crate::Error::Value(e.to_string()))?;
        for t in &tools {
            t.validate()?;
        }
        Ok(Self { tools })
    }
}

/// ALPHA-T's ATOOLIB.TL1 (vendor/AlphaT): number, name, nose radius (mm), front / rear clearance angles
const ATOOLIB: &[(i32, &str, f64, f64, f64)] = &[
    (1, "80 Deg Rougher 0.8R", 0.8, 87.0, 7.0),
    (2, "60 Deg Rougher 0.8R", 0.8, 87.0, 27.0),
    (3, "55 Deg Finisher 0.4R", 0.4, 87.0, 31.0),
    (4, "35 Deg Finisher 0.4R", 0.4, 87.0, 51.0),
    (5, "80 L.H. Rougher 0.8R", 0.8, 93.0, 173.0),
    (6, "55 L.H.Finisher 0.4R", 0.4, 93.0, 149.0),
    (7, "35 L.H.Finisher 0.4R", 0.4, 93.0, 129.0),
    (8, "60 Deg Neutral Tool", 0.8, 120.0, 60.0),
    (9, "55 Deg Neutral tool", 0.4, 118.0, 62.0),
    (10, "35 Deg Neutral Tool", 0.0, 108.0, 72.0),
    (11, "80 Boring-Bar 0.8R", 0.8, -87.0, -7.0),
    (12, "55 Boring-Bar 0.4R", 0.4, -87.0, -31.0),
    (13, "35 Boring-Bar 0.4R", 0.4, -72.0, -36.0),
    (14, "2mm Internal Groove", 0.0, 0.0, 0.0),
    (15, "3mm Internal Groove", 0.0, 0.0, 0.0),
    (16, "2mm Groove/Parting", 0.0, 0.0, 0.0),
    (17, "4mm Groove/Parting", 0.0, 0.0, 0.0),
    (18, "5mm Groove/Parting", 0.0, 0.0, 0.0),
    (19, "55 Deg Face Neutral", 0.4, 28.0, -28.0),
    (20, "35 Deg Face Neutral", 0.4, 18.0, -18.0),
    (21, "3mm Face Grooving.", 0.2, 0.0, 0.0),
    (22, "5mm Ball-End Facing", 2.5, 0.0, 0.0),
    (23, "5mm Ball-End Profile", 2.5, 0.0, 0.0),
    (24, "25mm Drill", 0.0, 0.0, 0.0),
    (25, "20mm Drill", 0.0, 0.0, 0.0),
    (26, "15mm Drill", 0.0, 0.0, 0.0),
    (27, "10mm Drill", 0.0, 0.0, 0.0),
    (28, "5mm Drill", 0.0, 0.0, 0.0),
    (29, "4mm Drill", 0.0, 0.0, 0.0),
    (30, "2mm Drill", 0.0, 0.0, 0.0),
    (31, "Threading Tool- Ext.", 0.0, 0.0, 0.0),
    (32, "Threading Tool- Int.", 0.0, 0.0, 0.0),
];

/// A tool from an ATTOOLS record (name, radius, clearance angles): type and hand guessed from the name.
pub fn tool_from_record(number: i32, name: &str, nose_radius: f64, front: f64, rear: f64, description: &str) -> Tool {
    let low = name.to_lowercase();
    let mut kind = if low.contains("groov") || low.contains("parting") {
        "groove"
    } else if low.contains("boring") {
        "bore"
    } else if low.contains("drill") {
        "drill"
    } else if low.contains("thread") {
        "thread"
    } else {
        "turn"
    };
    if kind == "turn" && front < 0.0 {
        kind = "bore";
    }
    let hand = if low.contains("l.h.") { "L" } else if low.contains("neutral") { "N" } else { "R" };
    let is_digits = |w: &str| !w.is_empty() && w.chars().all(|c| c.is_ascii_digit());
    let angle = name.split_whitespace().find(|w| is_digits(w)).map_or(0.0, |w| w.parse().unwrap());
    let digits = name.split("mm").next().unwrap_or("");
    let width = if matches!(kind, "groove" | "drill") && name.contains("mm") && is_digits(digits) {
        digits.parse().unwrap()
    } else {
        0.0
    };
    Tool {
        kind: kind.into(),
        number,
        name: name.into(),
        nose_radius,
        angle,
        width,
        hand: hand.into(),
        front_angle: front,
        rear_angle: rear,
        description: description.into(),
        ..Tool::default()
    }
}

/// The ATOOLIB tools ALPHA-T ships with. Nose radii are in mm.
pub fn standard_library() -> &'static ToolLibrary {
    static LIB: OnceLock<ToolLibrary> = OnceLock::new();
    LIB.get_or_init(|| ToolLibrary {
        tools: ATOOLIB.iter().map(|&(n, name, r, f, rear)| tool_from_record(n, name, r, f, rear, "")).collect(),
    })
}

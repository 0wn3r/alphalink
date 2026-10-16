//! One struct per operation; parameters mirror the ALPHA-T menus (DESIGN.md 4.3).
//!
//! Values are in drawing units (unscaled). The PRG post applies the machine profile's Z/feed scaling.

use serde::{Deserialize, Serialize};

use super::geometry::Point;

/// PRG #127 operation kinds (data/program_formats.json, "N20 block (operation)").
pub const OP_KINDS: &[(i32, &str)] = &[
    (1, "tool_change"),
    (6, "area_clear"),
    (7, "area_clear_internal"),
    (8, "face_clear"),
    (9, "groove"),
    (10, "area_prefinish"),
    (11, "area_prefinish_internal"),
    (12, "face_prefinish"),
    (14, "finish"),
    (15, "finish_internal"),
    (16, "finish_face"),
    (17, "groove_finish"),
    (18, "drill"),
    (20, "thread"),
    (21, "thread_spring"),
];

fn tool_change_point() -> Point {
    Point::new(100.0, 50.0)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct ToolChange {
    pub tool: i32,
    pub change_point: Point,
    pub feed: f64,
}

impl Default for ToolChange {
    fn default() -> Self {
        Self { tool: 1, change_point: tool_change_point(), feed: 0.15 }
    }
}

/// `offset_x` is a radius (per side) allowance, like [`FinishPass`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct AreaClear {
    pub tool: i32,
    pub start: Point,
    pub contour: Vec<String>,
    pub depth_of_cut: f64,
    pub offset_x: f64,
    pub offset_z: f64,
    pub feed: f64,
    pub towards: bool,
    pub pre_finish: bool,
    pub external: bool,
    /// ALPHA-T FRONT/FACE toggle; FACE (SD1 E101) cuts radial passes like face clear, #127=8 (7 internal)
    pub front: bool,
    /// RETRACT DIST in drawing units; None = ALPHA-T's default 1 mm (SD1 R)
    pub retract: Option<f64>,
    /// FOLLOW: each pass also feeds up the shoulder it ends at (SD1 E300)
    pub follow: bool,
}

impl Default for AreaClear {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            contour: vec![],
            depth_of_cut: 2.0,
            offset_x: 0.0,
            offset_z: 0.0,
            feed: 0.15,
            towards: true,
            pre_finish: false,
            external: true,
            front: true,
            retract: None,
            follow: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct FaceClear {
    pub tool: i32,
    pub start: Point,
    pub contour: Vec<String>,
    pub depth_of_cut: f64,
    pub offset_x: f64,
    pub offset_z: f64,
    pub feed: f64,
    pub towards: bool,
    pub pre_finish: bool,
}

impl Default for FaceClear {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            contour: vec![],
            depth_of_cut: 2.0,
            offset_x: 0.0,
            offset_z: 0.0,
            feed: 0.15,
            towards: true,
            pre_finish: false,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Datum {
    #[default]
    Left,
    Right,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum GrooveMode {
    #[default]
    Area,
    Finish,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Groove {
    pub tool: i32,
    pub start: Point,
    pub contour: Vec<String>,
    pub datum: Datum,
    pub finish_offset_x: f64,
    pub finish_offset_z: f64,
    pub peck_depth: f64,
    pub stepover: f64,
    pub tip_width: f64,
    pub feed: f64,
    /// ALPHA-T default FULL RET NO (SD1 L110; L111 = yes)
    pub full_retract: bool,
    pub external: bool,
    pub front: bool,
    pub z_shift: f64,
    pub repeat: i32,
    pub mode: GrooveMode,
    pub dwell_revs: f64,
}

impl Default for Groove {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            contour: vec![],
            datum: Datum::Left,
            finish_offset_x: 0.0,
            finish_offset_z: 0.0,
            peck_depth: 0.0,
            stepover: 2.0,
            tip_width: 3.0,
            feed: 0.15,
            full_retract: false,
            external: true,
            front: true,
            z_shift: 0.0,
            repeat: 0,
            mode: GrooveMode::Area,
            dwell_revs: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct FinishPass {
    pub tool: i32,
    pub start: Point,
    pub contour: Vec<String>,
    pub feed: f64,
    /// radius (per side): X moves by 2 * offset_x
    pub offset_x: f64,
    pub offset_z: f64,
    pub external: bool,
    /// ALPHA-T's third EXTERNAL/INTERNAL/FACE choice: #127=16, SD1 G70 E2, pull-off inwards (-X)
    pub face: bool,
}

impl Default for FinishPass {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            contour: vec![],
            feed: 0.15,
            offset_x: 0.0,
            offset_z: 0.0,
            external: true,
            face: false,
        }
    }
}

/// `depth` is the absolute Z of the hole bottom (ALPHA-T asks for the Z, e.g. -30).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Drill {
    pub tool: i32,
    pub start: Point,
    pub depth: f64,
    pub peck: f64,
    pub feed: f64,
    pub dwell_revs: f64,
    /// FULL RET: out to the start Z after every peck (DRILL_fullret)
    pub full_retract: bool,
    /// RETRACT DIST in drawing units; None = ALPHA-T's default 2 mm (DRILL_retract1: 1)
    pub retract: Option<f64>,
}

impl Default for Drill {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            depth: 0.0,
            peck: 0.0,
            feed: 0.15,
            dwell_revs: 0.0,
            full_retract: false,
            retract: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Thread {
    pub tool: i32,
    pub start: Point,
    pub outer_line: String,
    pub inner_line: String,
    /// > 0 (V1.21)
    pub pitch: f64,
    /// first cut (radius); cut n goes to first * sqrt(n), constant chip area
    pub cut_depth: f64,
    /// NO. OF CUTS mode: > 0 sets the first cut to depth / sqrt(cuts) (THREAD_6cuts: 10 / sqrt 6 = 4.0825)
    pub cuts: i32,
    /// ALPHA-T default: passes start this far in front of the thread (+Z)
    pub lead_in: f64,
    /// the thread runs this far past the drawn end
    pub lead_out: f64,
    /// ALPHA-T "Winkel": 0 = straight (radial) infeed; > 0 moves each cut's start (THREAD_angle30)
    pub angle: f64,
    pub external: bool,
    pub towards: bool,
    /// SPRING PASS: extra cuts at the final diameter, each in the #127=21 block (THREAD_spring2)
    pub spring_passes: i32,
}

impl Default for Thread {
    fn default() -> Self {
        Self {
            tool: 1,
            start: Point::default(),
            outer_line: String::new(),
            inner_line: String::new(),
            pitch: 1.0,
            cut_depth: 1.0,
            cuts: 0,
            lead_in: 5.0,
            lead_out: 0.0,
            angle: 0.0,
            external: true,
            towards: true,
            spring_passes: 0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Dwell {
    pub revs: f64,
}

impl Default for Dwell {
    fn default() -> Self {
        Self { revs: 1.0 }
    }
}

/// Move or rapid to a point; appended to the current tool block in the PRG.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct MoveTo {
    pub point: Point,
    pub rapid: bool,
    pub feed: f64,
}

impl Default for MoveTo {
    fn default() -> Self {
        Self { point: Point::default(), rapid: true, feed: 0.15 }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Operation {
    ToolChange(ToolChange),
    AreaClear(AreaClear),
    FaceClear(FaceClear),
    Groove(Groove),
    #[serde(rename = "finish")]
    FinishPass(FinishPass),
    Drill(Drill),
    Thread(Thread),
    Dwell(Dwell),
    #[serde(rename = "move")]
    MoveTo(MoveTo),
}

impl Operation {
    /// The "type" key in the .alcam file.
    pub fn type_name(&self) -> &'static str {
        match self {
            Operation::ToolChange(_) => "tool_change",
            Operation::AreaClear(_) => "area_clear",
            Operation::FaceClear(_) => "face_clear",
            Operation::Groove(_) => "groove",
            Operation::FinishPass(_) => "finish",
            Operation::Drill(_) => "drill",
            Operation::Thread(_) => "thread",
            Operation::Dwell(_) => "dwell",
            Operation::MoveTo(_) => "move",
        }
    }

    /// The tool of a cutting operation or tool change.
    pub fn tool(&self) -> Option<i32> {
        Some(match self {
            Operation::ToolChange(o) => o.tool,
            Operation::AreaClear(o) => o.tool,
            Operation::FaceClear(o) => o.tool,
            Operation::Groove(o) => o.tool,
            Operation::FinishPass(o) => o.tool,
            Operation::Drill(o) => o.tool,
            Operation::Thread(o) => o.tool,
            Operation::Dwell(_) | Operation::MoveTo(_) => return None,
        })
    }

    pub fn start(&self) -> Option<Point> {
        Some(match self {
            Operation::AreaClear(o) => o.start,
            Operation::FaceClear(o) => o.start,
            Operation::Groove(o) => o.start,
            Operation::FinishPass(o) => o.start,
            Operation::Drill(o) => o.start,
            Operation::Thread(o) => o.start,
            Operation::ToolChange(_) | Operation::Dwell(_) | Operation::MoveTo(_) => return None,
        })
    }

    pub fn feed(&self) -> Option<f64> {
        Some(match self {
            Operation::ToolChange(o) => o.feed,
            Operation::AreaClear(o) => o.feed,
            Operation::FaceClear(o) => o.feed,
            Operation::Groove(o) => o.feed,
            Operation::FinishPass(o) => o.feed,
            Operation::Drill(o) => o.feed,
            Operation::MoveTo(o) => o.feed,
            Operation::Thread(_) | Operation::Dwell(_) => return None,
        })
    }

    /// The picked contour of a contour operation.
    pub fn contour(&self) -> Option<&[String]> {
        match self {
            Operation::AreaClear(o) => Some(&o.contour),
            Operation::FaceClear(o) => Some(&o.contour),
            Operation::Groove(o) => Some(&o.contour),
            Operation::FinishPass(o) => Some(&o.contour),
            _ => None,
        }
    }

    /// The PRG #127 kind of its main block (None for moves and dwells).
    pub fn op_kind(&self) -> Option<i32> {
        Some(match self {
            Operation::ToolChange(_) => 1,
            Operation::AreaClear(o) => if o.external { 6 } else { 7 },
            Operation::FaceClear(_) => 8,
            Operation::Groove(_) => 9,
            Operation::FinishPass(o) => if o.face { 16 } else if o.external { 14 } else { 15 },
            Operation::Drill(_) => 18,
            Operation::Thread(_) => 20,
            Operation::Dwell(_) | Operation::MoveTo(_) => return None,
        })
    }
}

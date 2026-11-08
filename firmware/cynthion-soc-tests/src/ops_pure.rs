//! The pure half of `cynthion-soc/src/ops/`, as `crate::ops` (#303).
//!
//! Siblings reach each other as `super::proto`, so they must sit under one
//! module here exactly as they do in the firmware. The glue (`ops.rs`) and the
//! board driver (`ops/board.rs`) need the target and are not listed.

#[path = "../../cynthion-soc/src/ops/proto.rs"]
pub mod proto;

#[path = "../../cynthion-soc/src/ops/frame.rs"]
pub mod frame;

#[path = "../../cynthion-soc/src/ops/dispatch.rs"]
pub mod dispatch;

#[path = "../../cynthion-soc/src/ops/engine.rs"]
pub mod engine;

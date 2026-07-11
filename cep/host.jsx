// LUT Match — ExtendScript host (runs inside Premiere Pro's scripting engine).
// ES3 syntax only. Every function returns a plain string: "ok"/"pong" or "error: …".

function lmPing() {
  return "pong";
}

// Find the selected video clip in the active sequence.
// Returns {trackIndex, clip} or null.
function lmFindSelectedClip(seq) {
  for (var t = 0; t < seq.videoTracks.numTracks; t++) {
    var track = seq.videoTracks[t];
    for (var c = 0; c < track.clips.numItems; c++) {
      var clip = track.clips[c];
      if (clip.isSelected()) {
        return { trackIndex: t, clip: clip };
      }
    }
  }
  return null;
}

// Find a component (effect) on a clip by display name.
function lmFindComponent(clip, name) {
  for (var i = 0; i < clip.components.numItems; i++) {
    if (clip.components[i].displayName === name) return clip.components[i];
  }
  return null;
}

// Add "Lumetri Color" to the clip via the QE DOM (the only scripting way to
// add an effect). QE track items are indexed INCLUDING empty gaps, so match
// the DOM clip by its start ticks instead of by index.
function lmAddLumetri(seq, trackIndex, domClip) {
  app.enableQE();
  var qeSeq = qe.project.getActiveSequence();
  if (!qeSeq) return "error: QE has no active sequence";
  var qeTrack = qeSeq.getVideoTrackAt(trackIndex);
  if (!qeTrack) return "error: QE track not found";
  var wantTicks = String(domClip.start.ticks);
  for (var i = 0; i < qeTrack.numItems; i++) {
    var item = qeTrack.getItemAt(i);
    if (!item || item.type === "Empty") continue;
    if (item.start && String(item.start.ticks) === wantTicks) {
      var fx = qe.project.getVideoEffectByName("Lumetri Color");
      if (!fx) return "error: Lumetri Color effect not found in QE";
      item.addVideoEffect(fx);
      return "ok";
    }
  }
  return "error: could not locate the clip in QE track " + trackIndex;
}

// Apply the .cube at `path` as the Creative Look of the selected clip's
// Lumetri Color effect (adding the effect if it isn't there yet).
function lmApplyLut(path) {
  try {
    var seq = app.project.activeSequence;
    if (!seq) return "error: no active sequence";

    var found = lmFindSelectedClip(seq);
    if (!found) return "error: no video clip selected in the timeline";
    var clip = found.clip;

    var lumetri = lmFindComponent(clip, "Lumetri Color");
    if (!lumetri) {
      var added = lmAddLumetri(seq, found.trackIndex, clip);
      if (added !== "ok") return added;
      lumetri = lmFindComponent(clip, "Lumetri Color");
      if (!lumetri) return "error: Lumetri added but component not found";
    }

    // The Creative "Look" property accepts a LUT file path as its value.
    // Property naming varies across versions — try known display names.
    var names = ["Look", "Creative Look", "Input LUT"];
    for (var n = 0; n < names.length; n++) {
      for (var p = 0; p < lumetri.properties.numItems; p++) {
        var prop = lumetri.properties[p];
        if (prop.displayName === names[n]) {
          prop.setValue(path, true);
          return "ok";
        }
      }
    }

    // Nothing matched: report what IS there so failures are diagnosable.
    var propNames = [];
    for (var q = 0; q < lumetri.properties.numItems && q < 25; q++) {
      propNames.push(lumetri.properties[q].displayName);
    }
    return "error: no Look property found; Lumetri exposes: " + propNames.join(", ");
  } catch (e) {
    return "error: " + e.toString();
  }
}

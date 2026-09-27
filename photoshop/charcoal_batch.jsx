/*
 * charcoal_batch.jsx  —  Batch "charcoal drawing" for Adobe Photoshop
 * ----------------------------------------------------------------------------
 * Turns every image in a chosen folder into a charcoal-style drawing and saves
 * the results into a "charcoal" subfolder (PNG by default).
 *
 * IMPORTANT: this is a pixel FILTER, not a generative redraw. It therefore
 * NEVER alters the subject's morphology and never hallucinates — it only
 * restyles the existing pixels. (It won't match a true hand-drawn look; for
 * that, use the app's AI/Gemini engine.)
 *
 * HOW TO RUN
 *   Photoshop ▸ File ▸ Scripts ▸ Browse…  → pick this file.
 *   You'll be asked for the input folder; output goes to <input>/charcoal/.
 *
 * TUNE the CONFIG block below to taste, then re-run.
 *
 * Uses only documented ExtendScript DOM calls (no recorded Action needed), so
 * it works across recent Photoshop versions. Tested logic; verify on a couple
 * of images first and adjust CONFIG.
 */

#target photoshop

// ------------------------------- CONFIG -------------------------------------
var CONFIG = {
    blur:        12,        // line softness (Gaussian radius, px). Higher = softer.
    depth:       45,        // 0..100 tonal charcoal darkening (shadow strength).
    contrast:    25,        // -100..100 tonal contrast.
    grain:       12,        // 0..100 paper-grain noise amount (0 = none).
    whitePoint:  244,       // input highlight clipped to white (cleaner paper). 255 = off.
    maxEdge:     2400,      // longest edge in px (0 = keep original size).
    grayscale:   true,      // convert final to grayscale mode.
    outSubfolder:"charcoal",// results go into <input>/<outSubfolder>/
    format:      "png",     // "png" or "jpg"
    jpgQuality:  10,        // 1..12 (only for "jpg")
    suffix:      "_charcoal"
};
// ----------------------------------------------------------------------------

var RASTER = /\.(jpg|jpeg|png|tif|tiff|bmp|gif|psd)$/i;

function main() {
    var inFolder = Folder.selectDialog("Select the folder of photos to convert");
    if (!inFolder) return;

    var outFolder = new Folder(inFolder.fsName + "/" + CONFIG.outSubfolder);
    if (!outFolder.exists) outFolder.create();

    var files = inFolder.getFiles(function (f) {
        return (f instanceof File) && RASTER.test(f.name);
    });
    if (!files.length) {
        alert("No images found in:\n" + inFolder.fsName);
        return;
    }

    // Save + silence UI, then restore afterwards.
    var oldUnits = app.preferences.rulerUnits;
    var oldDialogs = app.displayDialogs;
    app.preferences.rulerUnits = Units.PIXELS;
    app.displayDialogs = DialogModes.NO;

    var ok = 0, fail = 0, errors = [];
    for (var i = 0; i < files.length; i++) {
        try {
            processOne(files[i], outFolder);
            ok++;
        } catch (e) {
            fail++;
            errors.push(decodeURI(files[i].name) + " — " + e);
        }
    }

    app.preferences.rulerUnits = oldUnits;
    app.displayDialogs = oldDialogs;

    var msg = "Charcoal batch complete.\n\nConverted: " + ok + "\nSkipped/failed: " + fail +
              "\nOutput: " + outFolder.fsName;
    if (errors.length) msg += "\n\n" + errors.join("\n");
    alert(msg);
}

function processOne(file, outFolder) {
    var doc = app.open(file);
    try {
        doc.flatten();
        if (doc.mode != DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);

        // Optional downscale (bounds memory/time on huge scans).
        if (CONFIG.maxEdge > 0) {
            var w = doc.width.as("px"), h = doc.height.as("px");
            var m = Math.max(w, h);
            if (m > CONFIG.maxEdge) {
                var s = CONFIG.maxEdge / m;
                doc.resizeImage(UnitValue(Math.round(w * s), "px"),
                                UnitValue(Math.round(h * s), "px"),
                                null, ResampleMethod.BICUBICSHARPER);
            }
        }

        var bg = doc.artLayers[0];

        // TONE: grayscale copy of the photo — the tonal/shading base.
        var tone = bg.duplicate();
        doc.activeLayer = tone;
        tone.desaturate();

        // SKETCH lines: dodge(gray, blurred-inverted-gray) -> crisp pencil lines.
        var sketch = tone.duplicate();
        var dodge = sketch.duplicate();
        doc.activeLayer = dodge;
        dodge.invert();
        dodge.blendMode = BlendMode.COLORDODGE;
        dodge.applyGaussianBlur(CONFIG.blur);
        var lines = dodge.merge();          // merge dodge into 'sketch' -> line layer

        // Multiply the lines over the tonal base = tonal charcoal with line detail.
        lines.blendMode = BlendMode.MULTIPLY;

        // Deepen shadows (depth) + tonal contrast on the tone layer.
        doc.activeLayer = tone;
        tone.adjustBrightnessContrast(-Math.round(CONFIG.depth * 0.4), CONFIG.contrast);

        // Paper grain: 50% gray Overlay layer + monochromatic noise.
        if (CONFIG.grain > 0) {
            var grainLayer = doc.artLayers.add();
            grainLayer.name = "grain";
            doc.activeLayer = grainLayer;
            var gray = new SolidColor();
            gray.rgb.red = gray.rgb.green = gray.rgb.blue = 128;
            doc.selection.selectAll();
            doc.selection.fill(gray);
            doc.selection.deselect();
            grainLayer.blendMode = BlendMode.OVERLAY;
            grainLayer.applyAddNoise(CONFIG.grain, NoiseDistribution.GAUSSIAN, true);
        }

        doc.flatten();

        // Clean paper: clip near-white to pure white.
        if (CONFIG.whitePoint > 0 && CONFIG.whitePoint < 255) {
            doc.activeLayer.adjustLevels(0, CONFIG.whitePoint, 1.0, 0, 255);
        }

        if (CONFIG.grayscale && doc.mode != DocumentMode.GRAYSCALE) {
            doc.changeMode(ChangeMode.GRAYSCALE);
        }

        saveResult(doc, outFolder, baseName(file));
    } finally {
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
}

function baseName(file) {
    var n = decodeURI(file.name);
    var dot = n.lastIndexOf(".");
    return (dot > 0 ? n.substring(0, dot) : n) + CONFIG.suffix;
}

function saveResult(doc, outFolder, stem) {
    if (CONFIG.format.toLowerCase() === "jpg") {
        var jpg = new JPEGSaveOptions();
        jpg.quality = CONFIG.jpgQuality;
        doc.saveAs(new File(outFolder.fsName + "/" + stem + ".jpg"), jpg, true, Extension.LOWERCASE);
    } else {
        var png = new PNGSaveOptions();
        doc.saveAs(new File(outFolder.fsName + "/" + stem + ".png"), png, true, Extension.LOWERCASE);
    }
}

main();

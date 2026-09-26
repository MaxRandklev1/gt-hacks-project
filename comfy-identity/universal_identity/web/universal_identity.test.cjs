// Run with Node; this .cjs file is not served as a ComfyUI extension module.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.value = ""; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.events[name] = callback; }
    click() { return this.events.click?.(); }
}
function walk(item) { return [item, ...item.children.flatMap(walk)]; }
const photo = (id) => ({ id, name: `${id}.jpg`, selected: true, width: 512, height: 512 });
let savedProfiles = [{ id: "person-one", name: "Person One", photos: [photo("a"), photo("b")], training: { adapter_available: false } }];
let savedJob = null;
let extension;
const calls = [];
const timers = new Map();
let nextTimer = 1;
const context = {
    document: { head: new Element("head"), createElement: (tag) => new Element(tag) },
    URL, FormData, File, console,
    setTimeout: (callback) => { const id = nextTimer++; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    app: { registerExtension: (value) => { extension = value; } },
    api: {
        apiURL: (value) => value,
        fetchApi: async (route, options) => {
            calls.push({ route, ...options });
            assert.equal(options.headers["X-Universal-Identity"], "1");
            let data;
            if (route.endsWith("/status")) data = { job: savedJob, active: Boolean(savedJob) };
            else if (options.method === "POST" && route.endsWith("/profiles")) {
                const files = options.body.getAll("files");
                const profile = { id: "person-two", name: options.body.get("name"), photos: files.map((file, index) => ({ ...photo(String(index)), name: file.name })), training: {} };
                savedProfiles.push(profile);
                data = { profile };
            } else if (options.method === "PATCH") {
                const profile = savedProfiles.find((item) => route.endsWith(item.id));
                const ids = JSON.parse(options.body).selected_photo_ids;
                for (const item of profile.photos) item.selected = ids.includes(item.id);
                data = { profile };
            } else if (options.method === "POST" && route.endsWith("/train")) {
                const steps = JSON.parse(options.body).steps;
                savedJob = { job_id: "job-1", profile_id: "person-two", status: "preparing", current_step: 0, max_steps: steps, message: "Preparing photos", warnings: [] };
                data = { job: savedJob };
            } else data = { profiles: savedProfiles };
            return { ok: true, json: async () => structuredClone(data) };
        },
    },
};
const source = fs.readFileSync(path.join(__dirname, "universal_identity.js"), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replaceAll("import.meta.url", '"http://localhost/extensions/universal_identity/universal_identity.js"');
vm.runInNewContext(source, context);

const node = {
    comfyClass: "UniversalIdentityProfile",
    size: [300, 200],
    widgets: [
        { name: "profile_id", value: "person-one", options: {} },
        { name: "reference_index", value: 1, options: {} },
        { name: "identity_strength", value: 0.8, options: {} },
        { name: "use_trained_identity", value: true, options: {} },
    ],
    graph: { change() {} },
    setSize(value) { this.size = value; },
    setDirtyCanvas() {},
    addDOMWidget(name, type, element, options) {
        const widget = { name, type, element, options };
        this.widgets.push(widget);
        return widget;
    },
};

(async () => {
    extension.nodeCreated(node);
    await new Promise(setImmediate);
    await extension.afterConfigureGraph();
    assert.equal(calls.filter((call) => call.method === "POST").length, 0, "Loading a workflow must not upload or train");
    const widget = node.widgets.find((item) => item.name === "identity_library");
    assert.equal(widget.serialize, false);
    assert.equal(widget.options.serialize, false);
    const find = (predicate) => walk(widget.element).find(predicate);
    const savedPerson = find((item) => item.tag === "select");
    assert.equal(savedPerson["aria-label"], "Saved person");
    assert.equal(savedPerson.value, "person-one", "Saved-person select reflects the loaded workflow");
    assert.equal(savedPerson.children.find((item) => item.value === "person-one").textContent, "Person One", "Show the person's name rather than their ID");

    const firstCheckbox = find((item) => item.type === "checkbox");
    firstCheckbox.checked = false;
    await firstCheckbox.events.change();
    assert.equal(node.widgets[1].value, 0, "Keep the same reference photo after removing an earlier photo");
    assert.equal(savedProfiles[0].photos[0].selected, false);
    const lastCheckbox = find((item) => item.type === "checkbox" && item.checked);
    lastCheckbox.checked = false;
    await lastCheckbox.events.change();
    assert.equal(savedProfiles[0].photos[1].selected, true, "Keep at least one reference photo");

    const input = find((item) => item.type === "file");
    const image = new File(["test"], "sample.jpg", { type: "image/jpeg" });
    Object.defineProperty(image, "webkitRelativePath", { value: "Person Two/sample.jpg" });
    const caption = new File(["ignored"], "sample.txt", { type: "text/plain" });
    input.files = [image, caption];
    await input.events.change();
    assert.equal(node.widgets[0].value, "person-two");
    assert.equal(savedProfiles[1].name, "Person Two");
    assert.equal(savedProfiles[1].photos.length, 1, "Only image files are uploaded");
    assert.equal(savedPerson.value, "person-two", "Upload selects the new saved person");
    assert.equal(calls.filter((call) => call.route.endsWith("/train")).length, 0, "Upload must not start training");

    savedPerson.value = "person-one";
    savedPerson.events.change();
    assert.equal(node.widgets[0].value, "person-one", "HTML selection updates the serialized profile widget");
    assert.equal(node.widgets[1].value, 0, "A different person starts at their first selected reference");
    node.widgets[0].value = "person-two";
    node.widgets[0].callback("person-two");
    assert.equal(savedPerson.value, "person-two", "Native widget changes update the HTML selection");
    savedProfiles[1].name = "Person Two Renamed";
    await extension.afterConfigureGraph();
    assert.equal(savedPerson.value, "person-two", "Refresh preserves the selected ID");
    assert.equal(savedPerson.children.find((item) => item.value === "person-two").textContent, "Person Two Renamed");
    let enterPrevented = false;
    let enterStopped = false;
    find((item) => item["aria-label"] === "New profile name").events.keydown({
        key: "Enter", preventDefault() { enterPrevented = true; }, stopPropagation() { enterStopped = true; },
    });
    assert(enterPrevented && enterStopped, "Enter in the profile name must not reach canvas shortcuts");

    await find((item) => item.tag === "button" && item.textContent === "Train identity").click();
    const trainCalls = calls.filter((call) => call.route.endsWith("/train"));
    assert.equal(trainCalls.length, 1);
    assert.equal(JSON.parse(trainCalls[0].body).steps, 400);
    assert.equal(timers.size, 1, "Poll only while a training job is active");
    assert.equal(find((item) => item.tag === "button" && item.textContent === "Training…").disabled, true);
    widget.onRemove();
    assert.equal(timers.size, 0, "Remove polling when the node is removed");
    console.log("PASS: saved-person select sync/labels/refresh; Enter shortcut isolation; no automatic training; upload/filter/name; selection/reference; explicit 400-step training; polling cleanup; nonserialized UI.");
})().catch((error) => { console.error(error); process.exitCode = 1; });

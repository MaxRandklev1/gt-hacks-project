import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const BASE = "/universal-identity";
const EMPTY_PROFILE = "(upload a profile)";
const panels = new Set();
const nodePanels = new WeakMap();
const activeStatuses = new Set(["preparing", "running"]);
let profiles = [];
let job = null;
let profileRequest = null;
let pollTimer = null;
let refreshedJobId = null;

const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = new URL("./universal_identity.css", import.meta.url).href;
document.head.append(stylesheet);

async function request(path, options = {}) {
    const response = await api.fetchApi(`${BASE}${path}`, {
        cache: "no-store",
        ...options,
        headers: { "X-Universal-Identity": "1", ...options.headers },
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || data.message || `Request failed (${response.status}).`);
    }
    return data;
}

function renderAll() {
    for (const panel of panels) panel.render();
}

async function refreshProfiles() {
    if (!profileRequest) {
        profileRequest = request("/profiles")
            .then((data) => {
                profiles = data.profiles;
                for (const panel of panels) panel.updateProfileOptions();
                renderAll();
            })
            .finally(() => { profileRequest = null; });
    }
    return profileRequest;
}

function schedulePoll() {
    clearTimeout(pollTimer);
    if (!panels.size || !activeStatuses.has(job?.status)) return;
    pollTimer = setTimeout(async () => {
        try {
            await refreshJob();
        } catch (error) {
            for (const panel of panels) {
                panel.showError(`Could not refresh training status: ${error.message}`);
            }
            schedulePoll();
        }
    }, 4000);
}

async function refreshJob() {
    const data = await request("/status");
    job = data.job;
    if (job && !activeStatuses.has(job.status) && refreshedJobId !== job.job_id) {
        await refreshProfiles();
        refreshedJobId = job.job_id;
    }
    renderAll();
    schedulePoll();
}

function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(text, className = "") {
    const item = element("button", className, text);
    item.type = "button";
    return item;
}

class IdentityPanel {
    constructor(node) {
        this.node = node;
        this.busy = false;
        this.disposed = false;
        this.photoSignature = "";
        this.profileOptionsSignature = "";
        this.profileWidget = node.widgets.find((widget) => widget.name === "profile_id");
        this.referenceWidget = node.widgets.find((widget) => widget.name === "reference_index");

        this.root = element("div", "universal-identity-panel");
        this.root.addEventListener("pointerdown", (event) => event.stopPropagation());
        this.root.addEventListener("keydown", (event) => event.stopPropagation());

        const heading = element("div", "identity-heading");
        heading.append(element("strong", "", "Identity library"));
        this.refreshButton = button("Refresh", "identity-secondary");
        this.refreshButton.addEventListener("click", () => this.refresh());
        heading.append(this.refreshButton);
        this.root.append(heading);

        const savedPerson = element("label", "identity-saved-person");
        savedPerson.append(element("span", "", "Saved person"));
        this.profileSelect = element("select");
        this.profileSelect.setAttribute("aria-label", "Saved person");
        this.profileSelect.addEventListener("change", () => {
            const profileId = this.profileSelect.value;
            if (profileId === this.profileWidget.value) return;
            this.setWidget(this.profileWidget, profileId);
            this.setWidget(this.referenceWidget, 0);
        });
        savedPerson.append(this.profileSelect);
        this.root.append(savedPerson);

        const upload = element("div", "identity-upload");
        this.nameInput = element("input");
        this.nameInput.type = "text";
        this.nameInput.maxLength = 80;
        this.nameInput.placeholder = "New profile name (or use the folder name)";
        this.nameInput.setAttribute("aria-label", "New profile name");
        this.nameInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                event.stopPropagation();
            }
        });
        this.uploadButton = button("Upload photo folder", "identity-secondary");
        this.fileInput = element("input");
        this.fileInput.type = "file";
        this.fileInput.multiple = true;
        this.fileInput.webkitdirectory = true;
        this.fileInput.setAttribute("webkitdirectory", "");
        this.fileInput.accept = ".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.avif";
        this.fileInput.hidden = true;
        this.uploadButton.addEventListener("click", () => this.fileInput.click());
        this.fileInput.addEventListener("change", () => this.uploadFiles());
        upload.append(this.nameInput, this.uploadButton, this.fileInput);
        this.root.append(upload);

        this.title = element("strong", "identity-profile-title");
        this.summary = element("div", "identity-muted");
        this.root.append(this.title, this.summary);
        this.photoHelp = element("div", "identity-muted", "Check photos to include in training. Click a photo to use it as the reference.");
        this.root.append(this.photoHelp);
        this.gallery = element("div", "identity-gallery");
        this.gallery.addEventListener("wheel", (event) => event.stopPropagation());
        this.root.append(this.gallery);

        const training = element("div", "identity-training");
        const stepsLabel = element("label", "identity-steps", "Training steps");
        this.stepsInput = element("input");
        this.stepsInput.type = "number";
        this.stepsInput.min = "1";
        this.stepsInput.max = "2000";
        this.stepsInput.step = "1";
        this.stepsInput.value = "400";
        this.stepsInput.addEventListener("input", () => this.render());
        stepsLabel.append(this.stepsInput);
        this.trainButton = button("Train identity", "identity-primary");
        this.trainButton.addEventListener("click", () => this.train());
        training.append(stepsLabel, this.trainButton);
        this.root.append(training);
        this.root.append(element("div", "identity-muted", "Train once, then use Run to generate with the saved identity."));

        this.status = element("div", "identity-status");
        this.status.setAttribute("role", "status");
        this.status.setAttribute("aria-live", "polite");
        this.progress = element("progress", "identity-progress");
        this.progress.setAttribute("aria-label", "Identity training progress");
        this.error = element("div", "identity-error");
        this.error.setAttribute("role", "alert");
        this.error.hidden = true;
        this.root.append(this.status, this.progress, this.error);

        this.details = element("details", "identity-details");
        this.details.append(element("summary", "", "Training details"));
        this.log = element("pre");
        this.details.append(this.log);
        this.root.append(this.details);

        this.widget = node.addDOMWidget("identity_library", "identity_library", this.root, {
            serialize: false,
            hideOnZoom: false,
            getMinHeight: () => 520,
            getMaxHeight: () => 720,
            getHeight: () => 560,
        });
        this.widget.serialize = false;
        const originalRemove = this.widget.onRemove;
        this.widget.onRemove = (...args) => {
            this.disposed = true;
            panels.delete(this);
            if (!panels.size) clearTimeout(pollTimer);
            originalRemove?.apply(this.widget, args);
        };

        for (const widget of [this.profileWidget, this.referenceWidget]) {
            const originalCallback = widget.callback;
            widget.callback = (...args) => {
                originalCallback?.apply(widget, args);
                this.clearError();
                this.render();
            };
        }
        this.profileWidget.label = "Identity profile";
        this.referenceWidget.label = "Reference photo (0 = first)";
        const strength = node.widgets.find((widget) => widget.name === "identity_strength");
        const useTrained = node.widgets.find((widget) => widget.name === "use_trained_identity");
        if (strength) strength.label = "Identity strength";
        if (useTrained) useTrained.label = "Use trained identity";

        panels.add(this);
        this.updateProfileOptions();
        this.render();
        node.setSize([Math.max(node.size[0], 460), Math.max(node.size[1], 780)]);
        this.refresh();
    }

    get profile() {
        return profiles.find((profile) => profile.id === this.profileWidget.value);
    }

    updateProfileOptions() {
        const values = profiles.length ? profiles.map((profile) => profile.id) : [EMPTY_PROFILE];
        const current = this.profileWidget.value;
        if (current && !values.includes(current)) values.unshift(current);
        this.profileWidget.options.values = values;
        this.node.setDirtyCanvas(true, true);
    }

    setWidget(widget, value) {
        widget.value = value;
        widget.callback?.(value);
        this.node.graph?.change();
        this.node.setDirtyCanvas(true, true);
    }

    syncSavedPeople() {
        const current = this.profileWidget.value || EMPTY_PROFILE;
        const missing = current !== EMPTY_PROFILE && !profiles.some((profile) => profile.id === current);
        const signature = JSON.stringify([profiles.map(({ id, name }) => [id, name]), missing ? current : null]);
        if (signature !== this.profileOptionsSignature) {
            this.profileOptionsSignature = signature;
            const placeholder = element("option", "", profiles.length ? "Choose a saved person" : "Upload a photo folder first");
            placeholder.value = EMPTY_PROFILE;
            placeholder.disabled = true;
            const options = [placeholder];
            if (missing) {
                const unavailable = element("option", "", "Saved person unavailable");
                unavailable.value = current;
                unavailable.disabled = true;
                options.push(unavailable);
            }
            for (const profile of profiles) {
                const option = element("option", "", profile.name);
                option.value = profile.id;
                options.push(option);
            }
            this.profileSelect.replaceChildren(...options);
        }
        this.profileSelect.value = current;
        this.profileSelect.disabled = this.busy || !profiles.length;
    }

    showError(message) {
        if (this.disposed) return;
        this.error.textContent = message;
        this.error.hidden = false;
    }

    clearError() {
        this.error.textContent = "";
        this.error.hidden = true;
    }

    async refresh() {
        this.clearError();
        try {
            await refreshProfiles();
            await refreshJob();
        } catch (error) {
            this.showError(error.message);
        }
    }

    async uploadFiles() {
        const allFiles = Array.from(this.fileInput.files || []);
        this.fileInput.value = "";
        if (!allFiles.length) return;
        const files = allFiles.filter((file) => /\.(png|jpe?g|webp|bmp|tiff?|avif)$/i.test(file.name));
        this.clearError();
        if (!files.length) return this.showError("This folder contains no supported photos.");
        if (files.some((file) => file.size > 20 * 1024 * 1024)) {
            return this.showError("Each photo must be 20 MB or smaller.");
        }
        if (files.reduce((sum, file) => sum + file.size, 0) > 200 * 1024 * 1024) {
            return this.showError("The photo folder must be 200 MB or smaller.");
        }
        const folderName = files[0].webkitRelativePath?.split("/")[0];
        const form = new FormData();
        form.append("name", this.nameInput.value.trim() || folderName || "New identity");
        for (const file of files) form.append("files", file, file.name);
        this.busy = true;
        this.render();
        this.status.textContent = `Uploading ${files.length} photos to this computer…`;
        try {
            const data = await request("/profiles", { method: "POST", body: form });
            await refreshProfiles();
            if (this.disposed) return;
            this.setWidget(this.profileWidget, data.profile.id);
            this.setWidget(this.referenceWidget, 0);
            this.nameInput.value = "";
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.busy = false;
            this.render();
        }
    }

    async selectPhoto(photo, selected) {
        const profile = this.profile;
        if (!profile || this.busy) return;
        const previousPhotos = profile.photos.filter((item) => item.selected);
        const previousReference = previousPhotos[Number(this.referenceWidget.value)]?.id;
        const selectedIds = profile.photos
            .filter((item) => item.id === photo.id ? selected : item.selected)
            .map((item) => item.id);
        if (!selectedIds.length) {
            this.showError("Keep at least one photo selected.");
            this.photoSignature = "";
            this.render();
            return;
        }
        this.clearError();
        this.busy = true;
        this.render();
        try {
            await request(`/profiles/${encodeURIComponent(profile.id)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ selected_photo_ids: selectedIds }),
            });
            if (this.profileWidget.value === profile.id) {
                this.setWidget(this.referenceWidget, Math.max(0, selectedIds.indexOf(previousReference)));
            }
            await refreshProfiles();
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.busy = false;
            this.photoSignature = "";
            this.render();
        }
    }

    async train() {
        const profile = this.profile;
        if (!profile || this.busy || activeStatuses.has(job?.status)) return;
        const steps = Number(this.stepsInput.value);
        this.clearError();
        if (!Number.isInteger(steps) || steps < 1 || steps > 2000) {
            return this.showError("Enter a whole number from 1 to 2000 training steps.");
        }
        this.busy = true;
        this.render();
        try {
            const data = await request(`/profiles/${encodeURIComponent(profile.id)}/train`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ steps }),
            });
            job = data.job;
            renderAll();
            schedulePoll();
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.busy = false;
            this.render();
        }
    }

    renderPhotos(profile, locked) {
        const selected = profile?.photos.filter((photo) => photo.selected) || [];
        const referenceIndex = Number(this.referenceWidget.value);
        this.referenceWidget.options.max = Math.max(0, selected.length - 1);
        const signature = JSON.stringify([profile?.id, profile?.photos, referenceIndex, locked]);
        if (signature === this.photoSignature) return;
        this.photoSignature = signature;
        this.gallery.replaceChildren();
        if (!profile) {
            this.gallery.append(element("div", "identity-empty", "Upload a folder of photos of one person, then review the photos here."));
            return;
        }
        for (const photo of profile.photos) {
            const index = selected.findIndex((item) => item.id === photo.id);
            const isReference = index >= 0 && index === referenceIndex;
            const card = element("div", `identity-photo${photo.selected ? "" : " identity-photo-excluded"}${isReference ? " identity-photo-reference" : ""}`);
            const preview = button("", "identity-photo-preview");
            preview.disabled = !photo.selected || locked;
            preview.title = photo.selected ? `Use ${photo.name} as reference` : "Include this photo to use it as reference";
            preview.setAttribute("aria-label", preview.title);
            preview.setAttribute("aria-pressed", String(isReference));
            const image = element("img");
            image.loading = "lazy";
            image.alt = photo.name;
            image.src = api.apiURL(`${BASE}/profiles/${encodeURIComponent(profile.id)}/photos/${encodeURIComponent(photo.id)}?thumbnail=1`);
            preview.append(image);
            if (isReference) preview.append(element("span", "identity-reference-badge", "Reference"));
            preview.addEventListener("click", () => this.setWidget(this.referenceWidget, index));
            const caption = element("label", "identity-photo-caption");
            const checkbox = element("input");
            checkbox.type = "checkbox";
            checkbox.checked = photo.selected;
            checkbox.disabled = locked;
            checkbox.setAttribute("aria-label", `Include ${photo.name} in training`);
            checkbox.addEventListener("change", () => this.selectPhoto(photo, checkbox.checked));
            const name = element("span", "", photo.name);
            name.title = photo.name;
            caption.append(checkbox, name);
            card.append(preview, caption);
            this.gallery.append(card);
        }
    }

    render() {
        if (this.disposed) return;
        this.syncSavedPeople();
        const profile = this.profile;
        const selectedCount = profile?.photos.filter((photo) => photo.selected).length || 0;
        const active = activeStatuses.has(job?.status);
        const ownJob = profile && job?.profile_id === profile.id ? job : null;
        const locked = this.busy || Boolean(active && ownJob);
        this.title.textContent = profile?.name || "Choose an identity profile";
        this.summary.textContent = profile
            ? `${selectedCount} of ${profile.photos.length} photos included · ${profile.training?.adapter_available ? "Trained identity ready" : "Not trained yet"}`
            : "Photos and training stay on this computer.";
        this.photoHelp.hidden = !profile;
        this.renderPhotos(profile, locked);
        this.uploadButton.disabled = this.busy;
        this.refreshButton.disabled = this.busy;
        this.nameInput.disabled = this.busy;
        this.stepsInput.disabled = this.busy || active;
        this.trainButton.disabled = this.busy || active || !profile || !selectedCount;
        this.trainButton.textContent = active && ownJob ? "Training…" : Number(this.stepsInput.value) === 2 ? "Run 2-step check" : "Train identity";
        this.progress.hidden = !ownJob || !active;
        this.details.hidden = !ownJob;
        if (ownJob) {
            this.status.textContent = ownJob.error || ownJob.message || ownJob.status;
            this.progress.max = Math.max(1, ownJob.max_steps || 400);
            this.progress.value = Math.min(this.progress.max, ownJob.current_step || 0);
            const log = Array.isArray(ownJob.log_tail) ? ownJob.log_tail.join("\n") : ownJob.log_tail || "";
            this.log.textContent = [...(ownJob.warnings || []), log].filter(Boolean).join("\n\n");
        } else if (active) {
            const trainingProfile = profiles.find((item) => item.id === job.profile_id);
            this.status.textContent = `Training ${trainingProfile?.name || "another profile"}. You can train this identity when it finishes.`;
        } else if (profile && selectedCount < 3) {
            this.status.textContent = "More varied, sharp photos usually improve identity learning.";
        } else {
            this.status.textContent = profile?.training?.adapter_available
                ? "Ready. Run uses this profile’s saved identity when Use trained identity is enabled."
                : "Review the photos, then train this identity. Run never starts training.";
        }
    }
}

app.registerExtension({
    name: "Local.UniversalIdentity",
    nodeCreated(node) {
        if (node.comfyClass !== "UniversalIdentityProfile" && node.constructor.comfyClass !== "UniversalIdentityProfile") return;
        if (nodePanels.has(node)) return;
        nodePanels.set(node, new IdentityPanel(node));
    },
    loadedGraphNode(node) {
        nodePanels.get(node)?.render();
    },
    async afterConfigureGraph() {
        if (!panels.size) return;
        try {
            await refreshProfiles();
            await refreshJob();
        } catch (error) {
            for (const panel of panels) panel.showError(error.message);
        }
    },
});

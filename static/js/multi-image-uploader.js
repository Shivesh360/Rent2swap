/**
 * Rent2Swap - Advanced Multi-Image Uploader
 * Features:
 * - Accumulative multi-file selection across multiple browse attempts
 * - Automatic deduplication & client-side photo optimization
 * - Main/cover image selection (reordering chosen image to index 0)
 * - Individual remove button on each photo & drag-and-drop support
 * - Object URL lifecycle cleanup to prevent memory leaks
 * - Frontend validation matching backend rules (JPG, PNG, WEBP, <=10MB/photo, <=8 photos)
 * - Seamless DataTransfer synchronization for form submission
 */

(function () {
  'use strict';

  class MultiImageUploader {
    constructor(options = {}) {
      this.inputId = options.inputId || 'item-images';
      this.previewContainerId = options.previewContainerId || 'image-preview-container';
      this.dropZoneId = options.dropZoneId || 'image-drop-zone';
      this.alertContainerId = options.alertContainerId || 'image-alert-container';
      this.statusBarId = options.statusBarId || 'image-status-bar';

      this.maxFiles = options.maxFiles || 8;
      this.maxFileSizeMB = options.maxFileSizeMB || 10;
      this.maxTotalSizeMB = options.maxTotalSizeMB || 25;
      this.allowedExtensions = ['jpg', 'jpeg', 'png', 'webp'];

      this.optimizeThresholdBytes = options.optimizeThresholdBytes || 250 * 1024;
      this.maxImageDimension = options.maxImageDimension || 1400;
      this.targetQuality = options.targetQuality || 0.80;

      this.input = document.getElementById(this.inputId);
      this.previewContainer = document.getElementById(this.previewContainerId);
      this.dropZone = document.getElementById(this.dropZoneId);
      this.alertContainer = document.getElementById(this.alertContainerId);
      this.statusBar = document.getElementById(this.statusBarId);

      this.selectedFiles = [];
      this.objectUrls = new Map();
      this.fileSignatures = new WeakMap();
      this._pendingOps = 0;
      this._readyResolvers = [];

      if (this.input) {
        this.form = this.input.closest('form');
        this.init();
      }
    }

    init() {
      this.input.addEventListener('change', () => {
        const incoming = Array.from(this.input.files || []);
        if (incoming.length > 0) {
          this.addFiles(incoming);
        }
      });

      if (this.dropZone) {
        ['dragenter', 'dragover'].forEach(eventName => {
          this.dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.dropZone.classList.add('is-dragover');
          });
        });

        ['dragleave', 'drop'].forEach(eventName => {
          this.dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            this.dropZone.classList.remove('is-dragover');
          });
        });

        this.dropZone.addEventListener('drop', (e) => {
          const dt = e.dataTransfer;
          if (dt && dt.files && dt.files.length > 0) {
            this.addFiles(Array.from(dt.files));
          }
        });

        this.dropZone.addEventListener('click', (e) => {
          if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A' && !e.target.closest('button')) {
            this.input.click();
          }
        });
      }

      if (this.form) {
        this.form.addEventListener('submit', () => {
          this.syncToInput();
        });
      }

      window.addEventListener('beforeunload', () => {
        this.objectUrls.forEach((url) => URL.revokeObjectURL(url));
        this.objectUrls.clear();
      });
    }

    isBusy() {
      return this._pendingOps > 0;
    }

    waitForReady() {
      if (this._pendingOps <= 0) {
        return Promise.resolve();
      }
      return new Promise((resolve) => {
        this._readyResolvers.push(resolve);
      });
    }

    getCoverPreviewUrl() {
      if (!this.selectedFiles.length) return null;
      const first = this.selectedFiles[0];
      if (!this.objectUrls.has(first)) {
        this.objectUrls.set(first, URL.createObjectURL(first));
      }
      return this.objectUrls.get(first) || null;
    }

    _notifyFilesChanged() {
      if (this.input) {
        this.input.dispatchEvent(new CustomEvent('r2s:files-changed', { bubbles: true }));
      }
    }

    _notifyStateChanged(busy) {
      if (this.input) {
        this.input.dispatchEvent(new CustomEvent('r2s:uploader-state', {
          bubbles: true,
          detail: { busy: Boolean(busy) }
        }));
      }
    }

    _getFileSignature(file) {
      return this.fileSignatures.get(file) || `${file.name}::${file.size}::${file.lastModified}`;
    }

    async compressImageIfNeeded(file) {
      if (!file || file.size <= this.optimizeThresholdBytes) {
        return file;
      }
      if (typeof document === 'undefined') {
        return file;
      }

      let bitmap = null;
      let objectUrl = null;
      try {
        let width = 0;
        let height = 0;
        let drawSource = null;

        if (typeof createImageBitmap === 'function') {
          try {
            bitmap = await createImageBitmap(file);
            width = bitmap.width;
            height = bitmap.height;
            drawSource = bitmap;
          } catch (_) {
            bitmap = null;
          }
        }

        if (!drawSource) {
          objectUrl = URL.createObjectURL(file);
          const img = await new Promise((resolve, reject) => {
            const el = new Image();
            el.onload = () => resolve(el);
            el.onerror = reject;
            el.src = objectUrl;
          });
          width = img.naturalWidth || img.width;
          height = img.naturalHeight || img.height;
          drawSource = img;
        }

        if (!width || !height) {
          return file;
        }

        const maxDim = Math.max(width, height);
        const needsResize = maxDim > this.maxImageDimension;
        const needsRecompress = file.size > 350 * 1024;
        if (!needsResize && !needsRecompress) {
          return file;
        }

        const scale = needsResize ? (this.maxImageDimension / maxDim) : 1;
        const targetW = Math.max(1, Math.round(width * scale));
        const targetH = Math.max(1, Math.round(height * scale));

        const canvas = document.createElement('canvas');
        canvas.width = targetW;
        canvas.height = targetH;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          return file;
        }
        ctx.drawImage(drawSource, 0, 0, targetW, targetH);

        const toBlob = (mime, quality) =>
          new Promise((resolve) => canvas.toBlob((b) => resolve(b), mime, quality));

        let outMime = 'image/webp';
        let outExt = 'webp';
        let blob = await toBlob('image/webp', this.targetQuality);
        if (!blob || blob.type !== 'image/webp') {
          outMime = 'image/jpeg';
          outExt = 'jpg';
          blob = await toBlob('image/jpeg', this.targetQuality);
        }

        if (blob && blob.size > 0 && blob.size < file.size * 0.92) {
          const baseName = (file.name || 'photo').replace(/\.[^.]+$/, '');
          const optimized = new File([blob], `${baseName}.${outExt}`, {
            type: outMime,
            lastModified: file.lastModified || Date.now()
          });
          this.fileSignatures.set(optimized, `${file.name}::${file.size}::${file.lastModified}`);
          return optimized;
        }
        return file;
      } catch (err) {
        return file;
      } finally {
        if (bitmap && typeof bitmap.close === 'function') {
          try { bitmap.close(); } catch (_) {}
        }
        if (objectUrl) {
          try { URL.revokeObjectURL(objectUrl); } catch (_) {}
        }
      }
    }

    async addFiles(files) {
      this.clearAlert();
      const candidateFiles = [];
      const warnings = [];

      const maxTotalBytes = this.maxTotalSizeMB * 1024 * 1024;
      let runningTotalBytes = this.selectedFiles.reduce((sum, f) => sum + f.size, 0);

      for (const file of files) {
        if (this.selectedFiles.length + candidateFiles.length >= this.maxFiles) {
          warnings.push(`You can upload up to ${this.maxFiles} photos per listing.`);
          break;
        }

        const ext = (file.name.split('.').pop() || '').toLowerCase();
        const isImageMime = file.type && file.type.startsWith('image/');
        const isAllowedExt = this.allowedExtensions.includes(ext);

        if (!isAllowedExt || (file.type && !isImageMime)) {
          warnings.push(`"${file.name}" is not supported. Please upload JPG, PNG, or WEBP images.`);
          continue;
        }

        const maxBytes = this.maxFileSizeMB * 1024 * 1024;
        if (file.size <= 0) {
          warnings.push(`"${file.name}" is empty (0 bytes) and was skipped.`);
          continue;
        }
        if (file.size > maxBytes) {
          warnings.push(`"${file.name}" exceeds the ${this.maxFileSizeMB}MB per-image limit.`);
          continue;
        }

        const sig = `${file.name}::${file.size}::${file.lastModified}`;
        const isDuplicate = this.selectedFiles.some(f => this._getFileSignature(f) === sig) ||
          candidateFiles.some(f => this._getFileSignature(f) === sig);

        if (isDuplicate) {
          warnings.push(`"${file.name}" has already been added.`);
          continue;
        }

        this.fileSignatures.set(file, sig);
        candidateFiles.push(file);
      }

      if (candidateFiles.length > 0) {
        this._pendingOps += 1;
        this._notifyStateChanged(true);
        try {
          const optimizedFiles = [];
          for (const rawFile of candidateFiles) {
            const processed = await this.compressImageIfNeeded(rawFile);
            if (runningTotalBytes + processed.size > maxTotalBytes) {
              warnings.push(`"${rawFile.name}" exceeds the ${this.maxTotalSizeMB}MB combined photo limit.`);
              continue;
            }
            runningTotalBytes += processed.size;
            optimizedFiles.push(processed);
          }

          if (optimizedFiles.length > 0) {
            this.selectedFiles.push(...optimizedFiles);
            this.render();
            this._notifyFilesChanged();
          }
        } finally {
          this._pendingOps = Math.max(0, this._pendingOps - 1);
          if (this._pendingOps === 0) {
            this._notifyStateChanged(false);
            if (this._readyResolvers.length > 0) {
              const resolvers = this._readyResolvers.splice(0, this._readyResolvers.length);
              resolvers.forEach(fn => fn());
            }
          }
        }
      }

      this.syncToInput();

      if (warnings.length > 0) {
        this.showAlert(warnings);
      }
    }

    setAsCover(index) {
      if (index <= 0 || index >= this.selectedFiles.length) return;
      const [chosen] = this.selectedFiles.splice(index, 1);
      this.selectedFiles.unshift(chosen);
      this.syncToInput();
      this.render();
      this._notifyFilesChanged();
    }

    validateBeforeSubmit() {
      if (this.isBusy()) {
        return { ok: false, message: 'Photos are still finishing preparation. Please wait a moment.' };
      }
      if (this.selectedFiles.length > this.maxFiles) {
        return { ok: false, message: `You can upload a maximum of ${this.maxFiles} photos per listing.` };
      }
      const maxBytes = this.maxFileSizeMB * 1024 * 1024;
      const maxTotalBytes = this.maxTotalSizeMB * 1024 * 1024;
      let total = 0;
      for (const file of this.selectedFiles) {
        const ext = (file.name.split('.').pop() || '').toLowerCase();
        if (!this.allowedExtensions.includes(ext)) {
          return { ok: false, message: `"${file.name}" is not a supported format (JPG, PNG, or WEBP required).` };
        }
        if (file.size <= 0) {
          return { ok: false, message: `"${file.name}" is empty (0 bytes). Please remove it and try again.` };
        }
        if (file.size > maxBytes) {
          return { ok: false, message: `"${file.name}" exceeds the ${this.maxFileSizeMB}MB per-photo limit.` };
        }
        total += file.size;
      }
      if (total > maxTotalBytes) {
        return { ok: false, message: `Selected photos exceed the ${this.maxTotalSizeMB}MB total upload limit. Please remove a photo.` };
      }
      return { ok: true };
    }

    removeFile(index) {
      if (index >= 0 && index < this.selectedFiles.length) {
        const removed = this.selectedFiles[index];
        if (this.objectUrls.has(removed)) {
          URL.revokeObjectURL(this.objectUrls.get(removed));
          this.objectUrls.delete(removed);
        }
        this.selectedFiles.splice(index, 1);
        this.syncToInput();
        this.render();
        this._notifyFilesChanged();
      }
    }

    clearAll() {
      this.objectUrls.forEach((url) => URL.revokeObjectURL(url));
      this.objectUrls.clear();
      this.selectedFiles = [];
      this.syncToInput();
      this.render();
      this.clearAlert();
      this._notifyFilesChanged();
    }

    syncToInput() {
      if (!this.input) return;
      try {
        const dt = new DataTransfer();
        this.selectedFiles.forEach(file => dt.items.add(file));
        this.input.files = dt.files;
      } catch (err) {
        console.warn('Could not sync DataTransfer to input.files:', err);
      }
    }

    formatBytes(bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    render() {
      if (!this.previewContainer) return;
      this.previewContainer.innerHTML = '';

      if (this.selectedFiles.length === 0) {
        if (this.statusBar) this.statusBar.classList.add('d-none');
        return;
      }

      let totalBytes = 0;

      this.selectedFiles.forEach((file, idx) => {
        totalBytes += file.size;

        if (!this.objectUrls.has(file)) {
          this.objectUrls.set(file, URL.createObjectURL(file));
        }
        const imgUrl = this.objectUrls.get(file);

        const card = document.createElement('div');
        card.className = 'r2u-thumb-card' + (idx === 0 ? ' is-cover' : '');

        const box = document.createElement('div');
        box.className = 'r2u-thumb-box';

        const img = document.createElement('img');
        img.src = imgUrl;
        img.alt = file.name;
        img.className = 'r2u-thumb-img';

        const badge = document.createElement('span');
        badge.className = idx === 0 ? 'r2u-badge-cover' : 'r2u-badge-index';
        badge.innerHTML = idx === 0 ? '<i class="bi bi-star-fill" aria-hidden="true"></i> Main' : `#${idx + 1}`;

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'r2u-remove-btn';
        removeBtn.title = `Remove ${file.name}`;
        removeBtn.setAttribute('aria-label', `Remove ${file.name}`);
        removeBtn.innerHTML = '<i class="bi bi-x" aria-hidden="true"></i>';
        removeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          this.removeFile(idx);
        });

        box.appendChild(img);
        box.appendChild(badge);
        box.appendChild(removeBtn);

        if (idx > 0) {
          const makeCoverBtn = document.createElement('button');
          makeCoverBtn.type = 'button';
          makeCoverBtn.className = 'r2u-cover-btn';
          makeCoverBtn.title = 'Set as main cover photo';
          makeCoverBtn.innerHTML = '<i class="bi bi-star" aria-hidden="true"></i> Set Main';
          makeCoverBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.setAsCover(idx);
          });
          box.appendChild(makeCoverBtn);
        }

        const label = document.createElement('div');
        label.className = 'r2u-thumb-name';
        label.title = file.name;
        label.textContent = file.name;

        const sizeLabel = document.createElement('div');
        sizeLabel.className = 'r2u-thumb-size';
        sizeLabel.textContent = this.formatBytes(file.size);

        card.appendChild(box);
        card.appendChild(label);
        card.appendChild(sizeLabel);

        this.previewContainer.appendChild(card);
      });

      if (this.statusBar) {
        this.statusBar.classList.remove('d-none');
        const countText = this.statusBar.querySelector('.photo-count-text');
        if (countText) {
          countText.innerHTML = `<strong>${this.selectedFiles.length}</strong> of ${this.maxFiles} photos (${this.formatBytes(totalBytes)}) &bull; Click <em>Set Main</em> on any photo to change cover`;
        }
      }
    }

    showAlert(messages) {
      if (!this.alertContainer) return;
      const msgList = Array.isArray(messages) ? messages : [String(messages || '')];
      this.alertContainer.textContent = '';

      const alertDiv = document.createElement('div');
      alertDiv.className = 'alert alert-warning alert-dismissible fade show py-2 px-3 small shadow-sm mb-2';
      alertDiv.setAttribute('role', 'alert');

      const row = document.createElement('div');
      row.className = 'd-flex align-items-center gap-2';

      const icon = document.createElement('i');
      icon.className = 'bi bi-exclamation-triangle-fill text-warning';
      row.appendChild(icon);

      const textWrap = document.createElement('div');
      msgList.forEach((msg, idx) => {
        if (idx > 0) textWrap.appendChild(document.createElement('br'));
        textWrap.appendChild(document.createTextNode(String(msg || '')));
      });
      row.appendChild(textWrap);
      alertDiv.appendChild(row);

      const closeBtn = document.createElement('button');
      closeBtn.type = 'button';
      closeBtn.className = 'btn-close py-2';
      closeBtn.setAttribute('data-bs-dismiss', 'alert');
      closeBtn.setAttribute('aria-label', 'Close');
      alertDiv.appendChild(closeBtn);

      this.alertContainer.appendChild(alertDiv);
    }

    clearAlert() {
      if (this.alertContainer) {
        this.alertContainer.innerHTML = '';
      }
    }
  }

  window.MultiImageUploader = MultiImageUploader;

  document.addEventListener('DOMContentLoaded', function () {
    const defaultInput = document.getElementById('item-images');
    if (defaultInput && document.getElementById('image-preview-container')) {
      window.r2sUploader = new MultiImageUploader({
        inputId: 'item-images',
        previewContainerId: 'image-preview-container',
        dropZoneId: 'image-drop-zone',
        alertContainerId: 'image-alert-container',
        statusBarId: 'image-status-bar'
      });
    }
  });
})();

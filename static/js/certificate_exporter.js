/**
 * CRMServer Certificate Exporter
 * 高解析度軟體授權證書 / 軟體保固證書 預覽、微調與匯出模組 (A4 橫式 2480x1754)
 */
(function (window) {
  "use strict";

  const CANVAS_W = 2480;
  const CANVAS_H = 1754;
  const SVG_W = 1600;
  const SVG_H = 1131;

  const FONT_ZH = '"Microsoft JhengHei", "PingFang TC", "Noto Sans TC", "Heiti TC", "Segoe UI", sans-serif';
  const FONT_EN = '"Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif';

  // Cache loaded images by key/url
  const imageCache = {};

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  /**
   * 產生極細緻同心弧線與幾何花紋 (Guilloche) 的 SVG 片段
   */
  function buildGuillocheArcs() {
    const parts = [];
    // 左側大弧線群
    for (let i = 0; i < 24; i++) {
      const r = 420 + i * 26;
      parts.push(
        `<circle cx="-120" cy="760" r="${r}" fill="none" stroke="#E2E6E0" stroke-width="1.15" opacity="0.72"/>`
      );
    }
    // 右側大弧線群
    for (let i = 0; i < 24; i++) {
      const r = 380 + i * 26;
      parts.push(
        `<circle cx="1720" cy="480" r="${r}" fill="none" stroke="#E2E6E0" stroke-width="1.15" opacity="0.72"/>`
      );
    }
    return parts.join("");
  }

  /**
   * 產生右側淡色防偽圓形花紋印記 (Rosette Watermark)
   */
  function buildRosetteSvg(cx, cy, outerR, ringText, opacity) {
    const petals = 36;
    const petalPaths = [];
    for (let i = 0; i < petals; i++) {
      const angle = (i * 360) / petals;
      petalPaths.push(
        `<ellipse cx="${cx}" cy="${cy}" rx="${outerR * 0.62}" ry="${outerR * 0.17}" fill="none" stroke="#85A456" stroke-width="1.1" transform="rotate(${angle} ${cx} ${cy})"/>`
      );
    }
    const textPathId = `rosetteTextPath_${Math.round(cx)}_${Math.round(cy)}`;
    const textR = outerR * 0.82;
    const repeatedText = `${ringText}   •   ${ringText}   •   ${ringText}   •   `;

    return `
      <g opacity="${opacity}">
        <circle cx="${cx}" cy="${cy}" r="${outerR}" fill="none" stroke="#85A456" stroke-width="1.6"/>
        <circle cx="${cx}" cy="${cy}" r="${outerR * 0.94}" fill="none" stroke="#85A456" stroke-width="1.0" stroke-dasharray="4 3"/>
        <circle cx="${cx}" cy="${cy}" r="${outerR * 0.72}" fill="none" stroke="#85A456" stroke-width="1.3"/>
        <circle cx="${cx}" cy="${cy}" r="${outerR * 0.66}" fill="none" stroke="#85A456" stroke-width="0.9"/>
        ${petalPaths.join("")}
        <defs>
          <path id="${textPathId}" d="M ${cx - textR},${cy} A ${textR},${textR} 0 1,1 ${cx + textR},${cy} A ${textR},${textR} 0 1,1 ${cx - textR},${cy}"/>
        </defs>
        <text font-family="Arial, Helvetica, sans-serif" font-size="${Math.round(outerR * 0.13)}" fill="#7B9B4C" letter-spacing="1.5">
          <textPath href="#${textPathId}" startOffset="0%">${repeatedText}</textPath>
        </text>
      </g>
    `;
  }

  /**
   * 產生底部中間綠色水平條紋裝飾
   */
  function buildBottomStripes() {
    const lines = [];
    for (let y = 1052; y <= 1112; y += 6) {
      lines.push(
        `<line x1="862" y1="${y}" x2="1188" y2="${y}" stroke="#A4BD7E" stroke-width="2.2"/>`
      );
    }
    return lines.join("");
  }

  /**
   * 產生右上角 PIXIS 向量 Logo (精簡銳利幾何字標)
   */
  function buildPixisLogo(x, y, scale) {
    return `
      <g transform="translate(${x}, ${y}) scale(${scale})">
        <!-- P -->
        <path d="M 0,62 L 0,10 C 0,4 4,0 10,0 L 42,0 C 58,0 68,10 68,24 C 68,38 58,48 42,48 L 16,48 L 16,62 C 16,66 13,69 8,69 C 3,69 0,66 0,62 Z M 16,14 L 16,34 L 40,34 C 47,34 52,30 52,24 C 52,18 47,14 40,14 Z" fill="#23272B"/>
        <!-- I -->
        <rect x="82" y="0" width="16" height="69" rx="8" fill="#23272B"/>
        <!-- X (Green stylized) -->
        <path d="M 114,4 C 117,0 123,0 127,4 L 180,58 C 184,62 184,67 180,70 C 176,73 170,73 166,69 L 114,15 C 110,11 110,7 114,4 Z" fill="#77A13B"/>
        <path d="M 180,4 C 184,7 184,12 180,16 L 127,69 C 123,73 117,73 114,70 C 110,67 110,62 114,58 L 166,4 C 170,0 176,0 180,4 Z" fill="#5A8527"/>
        <!-- I -->
        <rect x="196" y="0" width="16" height="69" rx="8" fill="#23272B"/>
        <!-- S -->
        <path d="M 284,12 C 284,16 280,19 275,19 L 245,19 C 239,19 236,21 236,25 C 236,29 239,31 245,31 L 269,31 C 283,31 292,39 292,50 C 292,62 283,69 268,69 L 231,69 C 226,69 223,66 223,61 C 223,57 226,54 231,54 L 267,54 C 273,54 276,52 276,48 C 276,44 273,42 267,42 L 243,42 C 229,42 220,34 220,23 C 220,11 229,3 244,3 L 275,3 C 280,3 284,7 284,12 Z" fill="#23272B"/>
      </g>
    `;
  }

  /**
   * 產生內建高解析度向量底圖 SVG (純底圖不含動態欄位文字，避免字型差異並提供極致銳利度)
   */
  function buildDefaultBgSvg(certType) {
    const isLicense = certType === "license";
    const titleEn = isLicense ? "Software License Certificate" : "Warranty Certificate";
    const titleZh = isLicense ? "軟　體　授　權　證　書" : "軟　體　保　固　證　書";
    const ruleX1 = isLicense ? 678 : 678;
    const ruleX2 = isLicense ? 868 : 868;

    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="${SVG_W}" height="${SVG_H}" viewBox="0 0 ${SVG_W} ${SVG_H}">
        <defs>
          <linearGradient id="bgDiag1" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#F6F8F5"/>
            <stop offset="50%" stop-color="#EDF2EA"/>
            <stop offset="100%" stop-color="#F7F9F6"/>
          </linearGradient>
          <linearGradient id="bgDiag2" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0%" stop-color="#F2F4F1" stop-opacity="0.7"/>
            <stop offset="100%" stop-color="#FFFFFF" stop-opacity="0.1"/>
          </linearGradient>
        </defs>

        <!-- Base White Paper -->
        <rect width="${SVG_W}" height="${SVG_H}" fill="#FFFFFF"/>

        <!-- Subtle Diagonal Geometric Ribbons -->
        <polygon points="0,160 1120,1131 680,1131 0,540" fill="url(#bgDiag2)"/>
        <polygon points="1050,62 1600,62 1600,320 720,1131 380,1131" fill="url(#bgDiag1)" opacity="0.65"/>

        <!-- Guilloche Curved Lines -->
        ${buildGuillocheArcs()}

        <!-- Top Green Segmented Bar -->
        <rect x="166" y="0" width="696" height="64" fill="#779F3D"/>
        <rect x="862" y="0" width="326" height="64" fill="#638A2E"/>
        <rect x="1188" y="0" width="412" height="64" fill="#4D731F"/>

        <!-- Top Left Titles -->
        <text x="166" y="158" font-family="Arial, 'Segoe UI', Helvetica, sans-serif" font-size="48" font-weight="700" fill="#23272B" letter-spacing="-0.3">${titleEn}</text>
        <text x="166" y="206" font-family="'Microsoft JhengHei', 'PingFang TC', 'Noto Sans TC', sans-serif" font-size="29" font-weight="500" fill="#7B828A" letter-spacing="4">${titleZh}</text>
        <line x1="${ruleX1}" y1="196" x2="${ruleX2}" y2="196" stroke="#8EB057" stroke-width="2.2"/>

        <!-- Top Right PIXIS Logo -->
        ${buildPixisLogo(1264, 114, 0.9)}

        <!-- Right Side Decorative Rosette Seals -->
        ${buildRosetteSvg(1188, 560, 108, titleEn, 0.14)}
        ${buildRosetteSvg(1388, 578, 152, titleEn, 0.24)}

        <!-- Bottom Left Subtle PIXIS Watermark -->
        <g opacity="0.11" transform="translate(166, 902) scale(1.62)">
          <path d="M 0,62 L 0,10 C 0,4 4,0 10,0 L 42,0 C 58,0 68,10 68,24 C 68,38 58,48 42,48 L 16,48 L 16,62 C 16,66 13,69 8,69 C 3,69 0,66 0,62 Z M 16,14 L 16,34 L 40,34 C 47,34 52,30 52,24 C 52,18 47,14 40,14 Z" fill="#6C7A68"/>
          <rect x="82" y="0" width="16" height="69" rx="8" fill="#6C7A68"/>
          <path d="M 114,4 C 117,0 123,0 127,4 L 180,58 C 184,62 184,67 180,70 C 176,73 170,73 166,69 L 114,15 C 110,11 110,7 114,4 Z" fill="#6C7A68"/>
          <path d="M 180,4 C 184,7 184,12 180,16 L 127,69 C 123,73 117,73 114,70 C 110,67 110,62 114,58 L 166,4 C 170,0 176,0 180,4 Z" fill="#6C7A68"/>
          <rect x="196" y="0" width="16" height="69" rx="8" fill="#6C7A68"/>
          <path d="M 284,12 C 284,16 280,19 275,19 L 245,19 C 239,19 236,21 236,25 C 236,29 239,31 245,31 L 269,31 C 283,31 292,39 292,50 C 292,62 283,69 268,69 L 231,69 C 226,69 223,66 223,61 C 223,57 226,54 231,54 L 267,54 C 273,54 276,52 276,48 C 276,44 273,42 267,42 L 243,42 C 229,42 220,34 220,23 C 220,11 229,3 244,3 L 275,3 C 280,3 284,7 284,12 Z" fill="#6C7A68"/>
        </g>

        <!-- Bottom Footer Bar -->
        <rect x="166" y="1048" width="696" height="68" fill="#2D3135"/>
        ${buildBottomStripes()}

        <!-- Bottom Right Company Signature (Clean space above for physical/digital stamp) -->
        <text x="1264" y="1072" font-family="'Microsoft JhengHei', 'PingFang TC', 'Noto Sans TC', sans-serif" font-size="25" font-weight="600" fill="#23272B" letter-spacing="1.5">飛泓科技股份有限公司</text>
        <text x="1264" y="1096" font-family="Arial, Helvetica, sans-serif" font-size="14" font-weight="400" fill="#767D84" letter-spacing="0.4">PIXIS Technology Co., Ltd.</text>
      </svg>
    `;
  }

  function loadImageFromSvg(cacheKey, svgString) {
    if (imageCache[cacheKey]) {
      return Promise.resolve(imageCache[cacheKey]);
    }
    return new Promise((resolve, reject) => {
      const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        imageCache[cacheKey] = img;
        resolve(img);
      };
      img.onerror = function (err) {
        URL.revokeObjectURL(url);
        reject(err);
      };
      img.src = url;
    });
  }

  function loadImageFromUrl(url) {
    if (imageCache[url]) {
      return Promise.resolve(imageCache[url]);
    }
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = function () {
        imageCache[url] = img;
        resolve(img);
      };
      img.onerror = function (err) {
        reject(err);
      };
      img.src = url;
    });
  }

  /**
   * 依據最大寬度自動將中英文混合字串斷行
   */
  function wrapText(ctx, text, maxWidth) {
    const str = String(text || "").trim();
    if (!str) return [""];
    const paragraphs = str.split(/\r?\n/);
    const lines = [];

    for (let p = 0; p < paragraphs.length; p++) {
      const para = paragraphs[p];
      if (!para) {
        lines.push("");
        continue;
      }
      let current = "";
      for (let i = 0; i < para.length; i++) {
        const ch = para[i];
        const test = current + ch;
        if (ctx.measureText(test).width > maxWidth && current.length > 0) {
          lines.push(current);
          current = ch;
        } else {
          current = test;
        }
      }
      if (current) {
        lines.push(current);
      }
    }
    return lines.length ? lines : [""];
  }

  /**
   * 將證書完整繪製到指定的 Canvas (2480 x 1754)
   */
  async function renderToCanvas(canvas, config) {
    const certType = config.cert_type === "warranty" ? "warranty" : "license";
    const useCustomBg = Boolean(config.use_custom_bg && config.custom_bg_url);
    const fields = config.fields || {};

    canvas.width = CANVAS_W;
    canvas.height = CANVAS_H;
    const ctx = canvas.getContext("2d");

    // 1. 白色底層 (確保 JPG 匯出不會變黑底)
    ctx.fillStyle = "#FFFFFF";
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

    // 2. 載入並繪製底圖
    let bgImg = null;
    try {
      if (useCustomBg) {
        bgImg = await loadImageFromUrl(config.custom_bg_url);
      } else {
        const svgStr = buildDefaultBgSvg(certType);
        bgImg = await loadImageFromSvg(`default_svg_${certType}`, svgStr);
      }
    } catch (e) {
      // Fallback to built-in SVG if custom bg fails
      const svgStr = buildDefaultBgSvg(certType);
      bgImg = await loadImageFromSvg(`default_svg_${certType}`, svgStr);
    }
    if (bgImg) {
      ctx.drawImage(bgImg, 0, 0, CANVAS_W, CANVAS_H);
    }

    // 3. 組合要繪製的欄位清單
    const isLicense = certType === "license";
    const rows = isLicense
      ? [
          { label: "產 品 名 稱", value: fields.product_name || "" },
          { label: "授 權 數 量", value: fields.quantity || "" },
          { label: "授 權 對 象", value: fields.recipient || "" },
          { label: "授 權 期 間", value: fields.period || "" },
          { label: "授 權 證 號", value: fields.cert_no || "" },
          { label: "授權開立日期", value: fields.issue_date || "", optional: true },
        ]
      : [
          { label: "產 品 名 稱", value: fields.product_name || "" },
          { label: "保 固 數 量", value: fields.quantity || "" },
          { label: "保 固 對 象", value: fields.recipient || "" },
          { label: "保 固 期 限", value: fields.period || "" },
          { label: "保 固 證 號", value: fields.cert_no || "" },
          { label: "保固開立日期", value: fields.issue_date || "", optional: true },
        ];

    const visibleRows = rows.filter((r) => !r.optional || String(r.value).trim() !== "");

    // 4. 計算版面座標 (比例對應 2480 x 1754)
    const barX = 258;
    const barW = 16;
    const labelX = 314;
    const colonX = 612;
    const valueX = 668;
    const maxValueW = CANVAS_W - valueX - 150;

    const fontSize = 46;
    const lineHeight = 68;
    const rowGap = 42;
    const startY = 568;

    ctx.textBaseline = "top";
    ctx.font = `500 ${fontSize}px ${FONT_ZH}`;

    // 先預計算各列行數以決定左側垂直綠線的精確高度
    const layoutRows = [];
    let cursorY = startY;
    for (let i = 0; i < visibleRows.length; i++) {
      const row = visibleRows[i];
      ctx.font = `500 ${fontSize}px ${FONT_ZH}`;
      const wrapped = wrapText(ctx, row.value, maxValueW);
      const blockHeight = Math.max(1, wrapped.length) * lineHeight;
      layoutRows.push({
        label: row.label,
        lines: wrapped,
        y: cursorY,
        height: blockHeight,
      });
      cursorY += blockHeight + rowGap;
    }

    // 繪製左側綠色垂直裝飾線（僅在使用內建底圖或自訂底圖皆繪製，若自訂底圖已有則依然對齊文字）
    if (layoutRows.length > 0) {
      const topY = startY - 14;
      const lastRow = layoutRows[layoutRows.length - 1];
      const bottomY = lastRow.y + lastRow.height + 10;
      ctx.fillStyle = "#729B37";
      ctx.fillRect(barX, topY, barW, bottomY - topY);
    }

    // 繪製每一列文字
    for (let i = 0; i < layoutRows.length; i++) {
      const item = layoutRows[i];
      // 標籤文字（左對齊均勻感，橄欖綠）
      ctx.fillStyle = "#5B822A";
      ctx.font = `500 ${fontSize}px ${FONT_ZH}`;
      ctx.fillText(item.label, labelX, item.y);
      ctx.fillText("：", colonX, item.y);

      // 內容數值（深炭黑）
      ctx.fillStyle = "#1D2126";
      ctx.font = `500 ${fontSize}px ${FONT_ZH}`;
      for (let l = 0; l < item.lines.length; l++) {
        ctx.fillText(item.lines[l], valueX, item.y + l * lineHeight);
      }
    }
  }

  /**
   * Modal 控制器
   */
  const Exporter = {
    state: null,
    modalEl: null,
    canvasEl: null,
    renderTimer: null,

    init: function () {
      this.modalEl = document.getElementById("cert-export-modal");
      this.canvasEl = document.getElementById("cert-preview-canvas");
      if (!this.modalEl || !this.canvasEl) return;

      // 綁定輸入即時預覽
      const inputIds = [
        "cert-field-product",
        "cert-field-quantity",
        "cert-field-recipient",
        "cert-field-period",
        "cert-field-certno",
        "cert-field-issuedate",
      ];
      inputIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
          el.addEventListener("input", () => this.scheduleRender());
        }
      });

      const bgToggle = document.getElementById("cert-use-custom-bg");
      if (bgToggle) {
        bgToggle.addEventListener("change", () => this.scheduleRender());
      }

      // ESC 鍵關閉 Modal
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && this.modalEl.classList.contains("is-open")) {
          this.close();
        }
      });

      // 點擊背景遮罩關閉
      this.modalEl.addEventListener("click", (e) => {
        if (e.target === this.modalEl) {
          this.close();
        }
      });

      // 全域事件委派：所有帶 data-cert-export 的按鈕皆可觸發
      document.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-cert-export]");
        if (!btn) return;
        e.preventDefault();
        const certType = btn.getAttribute("data-cert-export");
        const pk = btn.getAttribute("data-cert-id");
        if (certType && pk) {
          this.open(certType, pk);
        }
      });
    },

    open: async function (certType, pk) {
      if (!this.modalEl) this.init();
      if (!this.modalEl) return;

      this.setLoading(true);
      this.modalEl.classList.add("is-open");
      document.body.style.overflow = "hidden";

      try {
        const resp = await fetch(`/certificates/data/${encodeURIComponent(certType)}/${encodeURIComponent(pk)}/`, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
          throw new Error(data.error || "無法取得證書資料");
        }

        this.state = data;
        this.populateForm(data);
        await this.renderNow();
      } catch (err) {
        alert("載入證書資料失敗：" + err.message);
        this.close();
      } finally {
        this.setLoading(false);
      }
    },

    close: function () {
      if (!this.modalEl) return;
      this.modalEl.classList.remove("is-open");
      document.body.style.overflow = "";
    },

    setLoading: function (isLoading) {
      const loader = document.getElementById("cert-preview-loading");
      if (loader) {
        loader.style.display = isLoading ? "flex" : "none";
      }
    },

    populateForm: function (data) {
      const isLic = data.cert_type === "license";
      const titleEl = document.getElementById("cert-modal-title");
      if (titleEl) {
        titleEl.textContent = isLic ? "匯出軟體授權證書" : "匯出軟體保固證書";
      }

      // 動態調整左側表單標籤
      const labelMap = isLic
        ? {
            "cert-label-quantity": "授權數量",
            "cert-label-recipient": "授權對象",
            "cert-label-period": "授權期間",
            "cert-label-certno": "授權證號",
            "cert-label-issuedate": "授權開立日期（留空則不顯示）",
          }
        : {
            "cert-label-quantity": "保固數量",
            "cert-label-recipient": "保固對象",
            "cert-label-period": "保固期限",
            "cert-label-certno": "保固證號",
            "cert-label-issuedate": "保固開立日期（選填，留空則不顯示）",
          };
      Object.keys(labelMap).forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = labelMap[id];
      });

      const f = data.fields || {};
      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = val || "";
      };
      setVal("cert-field-product", f.product_name);
      setVal("cert-field-quantity", f.quantity);
      setVal("cert-field-recipient", f.recipient);
      setVal("cert-field-period", f.period);
      setVal("cert-field-certno", f.cert_no);
      setVal("cert-field-issuedate", f.issue_date);

      const bgWrap = document.getElementById("cert-custom-bg-wrap");
      const bgToggle = document.getElementById("cert-use-custom-bg");
      if (bgWrap && bgToggle) {
        if (data.has_custom_bg && data.custom_bg_url) {
          bgWrap.style.display = "flex";
          bgToggle.checked = true;
        } else {
          bgWrap.style.display = "none";
          bgToggle.checked = false;
        }
      }
    },

    getCurrentConfig: function () {
      const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value : "";
      };
      const bgToggle = document.getElementById("cert-use-custom-bg");
      return {
        cert_type: this.state ? this.state.cert_type : "license",
        custom_bg_url: this.state ? this.state.custom_bg_url : "",
        use_custom_bg: bgToggle ? bgToggle.checked : false,
        fields: {
          product_name: getVal("cert-field-product"),
          quantity: getVal("cert-field-quantity"),
          recipient: getVal("cert-field-recipient"),
          period: getVal("cert-field-period"),
          cert_no: getVal("cert-field-certno"),
          issue_date: getVal("cert-field-issuedate"),
        },
      };
    },

    scheduleRender: function () {
      clearTimeout(this.renderTimer);
      this.renderTimer = setTimeout(() => this.renderNow(), 80);
    },

    renderNow: async function () {
      if (!this.canvasEl || !this.state) return;
      await renderToCanvas(this.canvasEl, this.getCurrentConfig());
    },

    sendAuditLog: function (exportFormat) {
      if (!this.state) return;
      const cfg = this.getCurrentConfig();
      const csrftoken = getCookie("csrftoken");
      fetch("/certificates/audit/", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrftoken || "",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          cert_type: this.state.cert_type,
          object_id: this.state.object_id,
          export_format: exportFormat,
          cert_no: cfg.fields.cert_no,
          recipient: cfg.fields.recipient,
          product_name: cfg.fields.product_name,
        }),
      }).catch(() => {});
    },

    buildFilename: function (ext) {
      const cfg = this.getCurrentConfig();
      const prefix = cfg.cert_type === "warranty" ? "軟體保固證書" : "軟體授權證書";
      const raw = `${prefix}_${cfg.fields.cert_no || "cert"}_${cfg.fields.recipient || ""}`;
      const safe = raw.replace(/[\\/:*?"<>|\r\n\t]+/g, "_").replace(/_+$/g, "");
      return `${safe}.${ext}`;
    },

    downloadImage: async function (format) {
      if (!this.canvasEl || !this.state) return;
      await this.renderNow();

      const isJpg = format === "jpg" || format === "jpeg";
      const mime = isJpg ? "image/jpeg" : "image/png";
      const ext = isJpg ? "jpg" : "png";
      const quality = isJpg ? 0.96 : 1.0;

      const dataUrl = this.canvasEl.toDataURL(mime, quality);
      const link = document.createElement("a");
      link.download = this.buildFilename(ext);
      link.href = dataUrl;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      this.sendAuditLog(ext.toUpperCase());
    },

    printPdf: async function () {
      if (!this.canvasEl || !this.state) return;
      await this.renderNow();

      const dataUrl = this.canvasEl.toDataURL("image/png", 1.0);
      const title = this.buildFilename("pdf").replace(/\.pdf$/, "");
      const printWin = window.open("", "_blank");
      if (!printWin) {
        alert("瀏覽器封鎖了彈出視窗，請允許此網站開啟彈出視窗以列印 PDF。");
        return;
      }
      printWin.document.write(`
        <!DOCTYPE html>
        <html lang="zh-Hant">
        <head>
          <meta charset="utf-8" />
          <title>${title}</title>
          <style>
            @page { size: A4 landscape; margin: 0; }
            html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: #fff; }
            body { display: flex; align-items: center; justify-content: center; }
            img { width: 100%; height: auto; max-width: 297mm; max-height: 210mm; display: block; object-fit: contain; }
          </style>
        </head>
        <body>
          <img src="${dataUrl}" onload="setTimeout(function(){ window.print(); }, 150);" />
        </body>
        </html>
      `);
      printWin.document.close();

      this.sendAuditLog("PRINT/PDF");
    },

    renderPreviewCanvas: renderToCanvas,
  };

  document.addEventListener("DOMContentLoaded", function () {
    Exporter.init();
  });

  window.CertificateExporter = Exporter;
})(window);

# Auto_ext 文档索引

Auto_ext 是给 RFIC 验证流程用的自动化扩展工具：把 Cadence Calibre / Quantus / Jivaro / SI 这套抽 RC、跑 LVS、出 DSPF 的步骤用 `project.yaml` + `tasks.yaml` 描述出来，由一个 Python runner（CLI 或 PyQt5 GUI）按 task × stage Cartesian 展开调度。

文档不多但分工明确。**先看下面的决策树，找到你这次要看的那一篇就行**，别从头到尾全读一遍。

---

## 我要做什么？

```
我要做什么？
├─ 第一次在 Office 上把 Auto_ext 跑起来        →  OFFICE_QUICKSTART.md
├─ 已经能跑，要做回归 / 真 Cadence 验证          →  OFFICE_VALIDATION.md
├─ 配置 project.yaml 不知道字段什么意思           →  CONFIG_GLOSSARY.md
├─ 用 GUI 不知道某个 tab / 控件咋用              →  GUI_GUIDE.md
└─ 想看老脚本 (auto_ext.py + Run_ext.txt) 是啥样  →  archive/Old_project_prompt.txt
```

---

## 文档清单

| 文件 | 一句话说明 |
|---|---|
| [`OFFICE_QUICKSTART.md`](OFFICE_QUICKSTART.md) | 办公室 Linux 服务器上从 0 到 1 第一次跑通的最短路径：拉代码、装依赖、写最小 config、跑 dry-run。 |
| [`OFFICE_VALIDATION.md`](OFFICE_VALIDATION.md) | 已经能跑之后，回归 / 真 Cadence 环境验证的逐步清单：测试基线、GUI 起得来、并行 jobs、混合 pass/fail 等。 |
| [`CONFIG_GLOSSARY.md`](CONFIG_GLOSSARY.md) | `project.yaml` 每个字段的含义、自动反解来源、什么时候要手填。Phase 5.6.5 的 `paths` 段说明也在这里。 |
| [`GUI_GUIDE.md`](GUI_GUIDE.md) | PyQt5 GUI 五个 tab 的逐控件说明 + diff editor / preset picker / init wizard / template generator 等对话框入口。 |
| [`archive/`](archive/) | 已废弃 / 已被替代的文件归档于此，仅作参考，不属于现行工作流。 |

---

## 一些常见误区

- **不要混用 `OFFICE_QUICKSTART` 和 `OFFICE_VALIDATION`**：前者是"我怎么把它跑起来"，后者是"它对不对"。第一次部署看 quickstart；之后每次拉新代码做回归看 validation。
- **`CONFIG_GLOSSARY` 不是教程**：是字典。配置写不出来时拿它查字段，不是按顺序读完。
- **`GUI_GUIDE` 假设你 Windows 本地起 GUI**：服务器上 GUI 路径在 `OFFICE_VALIDATION.md` 的 GUI 那步。

# Trellis2 / Pixal3D image-to-3D — API workflow reference

`trellis2_api_workflow_reference.json` is the minimal, execution-validated
API-format ComfyUI graph for turning one image into a textured `.glb`, derived
from ComfyUI's shipped template
`python_embeded\Lib\site-packages\comfyui_workflow_templates_json\templates\3d_pixal3d_trellis2_image_to_model.json`
(66 UI nodes) by converting it mechanically to API format and then pruning it
down to the 38 nodes that are actual ancestors of the file-saving node.

It was validated node-for-node against ComfyUI's own
`execution.validate_prompt()` (run via the embedded Python, no server
started) with **zero structural errors** — the only validation failures are
the missing model files and the placeholder `LoadImage` filename, both
expected (see below).

## Is there a text prompt anywhere?

**No.** This pipeline is purely image-to-3D. There is no `CLIPTextEncode` (or
any text-input node) anywhere in the 66-node template — the only "prompt"
data is the input photo. Conditioning comes entirely from `Pixal3DConditioning`
(DINOv3 image embeddings + an estimated camera FOV), not text.

## The default branch: Pixal3D, not vanilla Trellis2

The template ships with a `PrimitiveBoolean` node titled *"Boolean (Switch to
Trellis2)"* wired into three `ComfySwitchNode`s that pick between a
`Pixal3DConditioning` path and a `Trellis2Conditioning` path (and between two
`UNETLoader` checkpoints, `pixal3d_int8_convrot.safetensors` vs.
`trellis_2_int8_convrot.safetensors`). Its default value is `false`, and
`ComfySwitchNode.execute()` selects `on_true` only `if switch` — so the
**Pixal3D path is what actually runs by default**. The reference JSON hardwires
that path and drops the switch nodes, the unused `Trellis2Conditioning` node,
and the unused `trellis_2_int8_convrot.safetensors` `UNETLoader` entirely
(they're template conveniences for A/B-ing the two models, not part of the
default output).

## Essential node chain (see the JSON's node ids)

```
LoadImage(122)
  -> RemoveBackground(192)  [uses LoadBackgroundRemovalModel(193) = birefnet.safetensors]
  -> ImageCropToMask(312)   [1024x1024, pad_factor=1.1 — Pixal3D's required padding]
  -> MoGeInference(56) [uses LoadMoGeModel(55) = moge_2_vitl_normal_fp16.safetensors]
  -> MoGeGeometryToFOV(242)                         -> camera_angle_x
  -> Pixal3DConditioning(298) [+ CLIPVisionLoader(15) = dino_v3_L_naf_fp32.safetensors]
       -> positive/negative conditioning

UNETLoader(319) = pixal3d_int8_convrot.safetensors
  -> CFGOverride(199) -> RescaleCFG(125) -> ModelSamplingSD3(108)   [structure-stage model]
  -> CFGOverride(279) -> RescaleCFG(126)                            [shape-stage model, x2 KSamplers]
  -> (used directly, no CFG override)                               [texture-stage model]

EmptyTrellis2LatentStructure(87) -> KSampler(3) [structure, 12 steps]
  -> VaeDecodeStructureTrellis2(119) [vae=VAELoader(117)=trellis_2_shape_vae_bf16.safetensors]
  -> Trellis2ShapeStage(91)
  -> KSampler(18) [shape @512, 20 steps]
  -> Trellis2UpsampleStage(94) [cascades to target_resolution=1536]
  -> KSampler(23) [shape @1536, 12 steps]
  -> Trellis2TextureStage(98)
  -> KSampler(12) [texture, 12 steps, model=UNETLoader(319) directly]

VaeDecodeShapeTrellis(92)   [samples=KSampler(23), vae=trellis_2_shape_vae] -> mesh, shape_subdivides
VaeDecodeTextureTrellis(93) [samples=KSampler(12), vae=VAELoader(118)=trellis_2_texture_vae_bf16.safetensors,
                              shape_subdivides=92] -> voxel_colors

# mesh post-processing / UV-texture bake (this is the branch that actually
# reaches the file-save node -- see "Two other mesh branches" below)
mesh(92) -> RemeshMesh(241) -> DecimateMesh(186) -> MeshSmoothNormals(238)
  -> UnwrapMesh(196)  [UV unwrap, resolution=4096]
  -> BakeTextureFromVoxel(147)   [mesh=196, voxel_colors=93, reference_mesh=92] -> base_color, metallic, roughness
  -> BakeNormalMapFromMesh(224)  [low_poly=196, high_poly=241] -> normal_map
  -> BakeAmbientOcclusion(233)   [low_poly=196, high_poly=241] -> occlusion
  -> ApplyTextureToMesh(210)     [mesh=196 + the 5 baked maps above]
  -> MeshSmoothNormals(260)
  -> SaveGLB(322)  <-- FINAL NODE, produces the .glb
```

`LoadImage(122)` is the **image input node** — set its `image` widget to the
filename of an image already uploaded into ComfyUI's `input/` folder (POST to
`/upload/image` first, then reference the returned filename here).

## Two other mesh branches in the *original* template that were dropped

The stock template actually builds **three** different meshes from the same
generation, and only one of them is wired to something that saves to disk:

1. `VoxelToMesh(4)` off the raw structure voxel → `MeshToFile3D(247)` →
   `Preview3DAdvanced(246)` — a crude low-res preview only, no save node
   downstream. Dropped.
2. `PaintMesh(252)` (vertex-color-only mesh, no UV unwrap/texture bake) →
   `MeshToFile3D(282)` → `Preview3DAdvanced(323)` — **also preview-only, not
   saved**. This is the "simple" chain one would expect the pipeline to end
   on (mesh + PaintMesh + export), but in the shipped template it only feeds
   a canvas preview, not the actual output node. Dropped.
3. The full UV-unwrap + texture-bake branch (`UnwrapMesh` → `BakeTextureFromVoxel`
   / `BakeNormalMapFromMesh` / `BakeAmbientOcclusion` → `ApplyTextureToMesh`) →
   `MeshToFile3D(285)` → `Save3DAdvanced(322)`, which has `is_output_node=True`
   and is the node that actually writes a file. **This is the branch kept.**

## Simplifications made versus a literal UI->API conversion

A newer ComfyUI change makes `PreviewImage` (and `MaskPreview`) into
pass-through nodes: `RETURN_TYPES = ("IMAGE",)` / `("MASK",)`, so the template
wires several of them into the *middle* of the real pipeline (e.g. the image
that reaches `Pixal3DConditioning` is nominally "from" a `PreviewImage` node,
and `ApplyTextureToMesh`'s `base_color`/`metallic`/`roughness`/`occlusion`/
`normal_map` inputs nominally come from `PreviewImage` nodes titled "Base
Color"/"Metallic"/"Roughness"/"Ambiant Occlusion"/"Normal"). Since
`PreviewImage.execute()` is an identity function on the tensor (its only
other effect is writing a preview PNG to ComfyUI's temp folder), the
reference JSON rewires every such consumer directly to the true producer
node and drops the passthrough entirely. Also dropped as pure identity
passthroughs: `GetMeshInfo` (mesh in == mesh out, plus a logging side
effect) and `PrimitiveInt` "Texture Resolution" (its value, 4096, is just
inlined into `UnwrapMesh.resolution` and `BakeTextureFromVoxel.texture_size`).
`RenderUVAtlas` + its `PreviewImage` (a UV-layout visualizer) are pure debug
output with no downstream consumer and were dropped outright.

**`Save3DAdvanced(322)` was swapped for `SaveGLB`.** The original template's
save node needs a `viewport_state` (`LOAD_3D` type — a Load3D-viewer UI
state blob, serialized here as just `""` in the template) plus optional
`model_3d_info`/`camera_info`, and its `/history` result is a bespoke
3-element list rather than the standard save-node shape (see below).
`SaveGLB` takes the `MESH` output directly (no `MeshToFile3D` conversion
node needed — it does the same GLB serialization internally, plus embeds
prompt/extra_pnginfo metadata `Save3DAdvanced` doesn't), needs no viewport
state, isn't marked `is_experimental`, and — most usefully here — its
`/history` output uses the same `{"filename", "subfolder", "type"}` shape
`comfyui_shared.py`'s existing `fetch_output_file()` helper already expects
for images. This is a deliberate substitution, not something the shipped
template does; if strict fidelity to the template's exact output node
matters, revert node `"322"` to `Save3DAdvanced` (inputs: `model_3d` from a
re-added `MeshToFile3D` fed by node `260`, `filename_prefix`,
`viewport_state=""`, `width=1024`, `height=1024`).

## What `/history/<prompt_id>` will show for the final node

With `SaveGLB` (as shipped in the reference JSON), node `"322"`'s entry in
the history response's `outputs` dict looks like:

```json
"322": {
  "3d": [
    {"filename": "trellis2_00001_.glb", "subfolder": "3d", "type": "output"}
  ]
}
```
(from `SaveGLB.execute()` returning `IO.NodeOutput(ui={"3d": results})` in
`comfy_extras/nodes_save_3d.py`) — the same shape family as `SaveImage`'s
`outputs[id]["images"]`, just under the `"3d"` key instead. Download it with
the existing `fetch_output_file(filename, subfolder, "output")` helper in
`comfyui_shared.py`.

If you instead revert to the template's original `Save3DAdvanced`, its shape
is different and easy to mis-parse: `execute_save_3d_advanced()` (also in
`nodes_save_3d.py`) returns
`ui=UI.PreviewUI3DAdvanced(model_file, camera_info, model_3d_info)`, whose
`as_dict()` (in `comfy_api/latest/_ui.py`) is:

```json
"322": {
  "result": ["3d/ComfyUI_00001.glb", <camera_info>, <model_3d_info>]
}
```

i.e. `outputs["322"]["result"][0]` is a single `"<subfolder>/<filename>"` (or
bare filename with no subfolder) string, saved into ComfyUI's normal output
directory (`type` is implicitly `"output"`, not present in the response) —
not a list of `{filename, subfolder, type}` dicts like every other save node.

## Missing model files (must be downloaded before this can run)

None of these exist yet under `O:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\`
— every relevant folder currently contains only the placeholder
`put_..._here` file (checked directly; also independently confirmed by
running the converted JSON through ComfyUI's own `execution.validate_prompt`,
which rejected exactly these six and nothing else). Download URLs are the
ones embedded in the template's own node `properties.models` metadata:

| Node (id) | Widget value | Target folder | Source URL |
|---|---|---|---|
| VAELoader (117) | `trellis_2_shape_vae_bf16.safetensors` | `models/vae/` | `https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors` |
| VAELoader (118) | `trellis_2_texture_vae_bf16.safetensors` | `models/vae/` | `https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors` |
| CLIPVisionLoader (15) | `dino_v3_L_naf_fp32.safetensors` | `models/clip_vision/` | `https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors` |
| LoadMoGeModel (55) | `moge_2_vitl_normal_fp16.safetensors` | `models/geometry_estimation/` | `https://huggingface.co/Comfy-Org/MoGe/resolve/main/geometry_estimation/moge_2_vitl_normal_fp16.safetensors` |
| LoadBackgroundRemovalModel (193) | `birefnet.safetensors` | `models/background_removal/` | `https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors` |
| UNETLoader (319) | `pixal3d_int8_convrot.safetensors` | `models/diffusion_models/` | `https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors` |

(Not needed for the default Pixal3D path, but present in the original
template for the Trellis2-branch toggle:
`trellis_2_int8_convrot.safetensors` at
`https://huggingface.co/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors`.)

## For the engineer wiring this up dynamically

- Upload the source image via ComfyUI's `/upload/image`, then set
  `prompt["122"]["inputs"]["image"]` to the returned filename before POSTing
  to `/prompt`.
- Everything else in the JSON is a concrete value already (seeds are fixed,
  not randomized — bump `prompt["3"]/["12"]/["18"]/["23"]["inputs"]["seed"]`
  per-request if you want variation across otherwise-identical inputs).
- `filename_prefix` on the `SaveGLB` node (`"322"`) controls the output
  subfolder/prefix ComfyUI saves under — currently `"3d/trellis2"`.

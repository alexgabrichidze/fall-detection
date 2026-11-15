// LiteRT.js validates fixed input shapes. This model already stores the
// concrete batch-one shapes alongside its TensorFlow shape signatures.
// Replace only dynamic signature dimensions, leaving weights and ops intact.
export function fixTensorSignatures(model) {
  function vector(table, field) {
    const vtable = table - model.readInt32LE(table);
    const offset = 4 + field * 2;
    if (offset >= model.readUInt16LE(vtable)) return null;
    const relative = model.readUInt16LE(vtable + offset);
    if (!relative) return null;
    const pointer = table + relative;
    const array = pointer + model.readUInt32LE(pointer);
    return { data: array + 4, length: model.readUInt32LE(array) };
  }
  function tableAt(array, index) {
    const pointer = array.data + index * 4;
    return pointer + model.readUInt32LE(pointer);
  }
  const graphs = vector(model.readUInt32LE(0), 2);
  for (let g = 0; g < graphs.length; g++) {
    const tensors = vector(tableAt(graphs, g), 0);
    for (let t = 0; t < tensors.length; t++) {
      const tensor = tableAt(tensors, t);
      const shape = vector(tensor, 0);
      const signature = vector(tensor, 7);
      if (!shape || !signature || shape.length !== signature.length) continue;
      for (let d = 0; d < shape.length; d++) {
        const dimension = model.readInt32LE(shape.data + d * 4);
        const index = signature.data + d * 4;
        if (model.readInt32LE(index) === -1 && dimension > 0) model.writeInt32LE(dimension, index);
      }
    }
  }
  return model;
}

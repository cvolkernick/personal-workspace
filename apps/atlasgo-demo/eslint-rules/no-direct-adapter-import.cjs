const FORBIDDEN_SOURCE =
  /(?:mock-source|live-source|alt-source|fleet-repository|expand-seed|load-seed|seed\/fleet\.json|data\/seed|fleet\.json)/;

const FORBIDDEN_NAMES = new Set([
  "MockDataSource",
  "LiveDataSource",
  "AltMemorySource",
  "FleetRepository",
  "loadSeedFile",
  "expandSeed",
]);

function checkSource(context, node, source) {
  if (typeof source === "string" && FORBIDDEN_SOURCE.test(source)) {
    context.report({
      node,
      message: "UI code must import the data interface, not a seed file or adapter.",
    });
  }
}

function checkSpecifiers(context, node) {
  for (const spec of node.specifiers ?? []) {
    const imported = spec.imported?.name ?? spec.local?.name;
    if (FORBIDDEN_NAMES.has(imported)) {
      context.report({
        node: spec,
        message: `Do not import ${imported} from UI code. Use the DataSource interface.`,
      });
    }
  }
}

const rule = {
  meta: {
    type: "problem",
    docs: { description: "Forbid UI imports of fleet seed files and data adapters." },
    schema: [],
  },
  create(context) {
    return {
      ImportDeclaration(node) {
        checkSource(context, node, node.source.value);
        checkSpecifiers(context, node);
      },
      ExportNamedDeclaration(node) {
        if (!node.source) return;
        checkSource(context, node, node.source.value);
        checkSpecifiers(context, node);
      },
      ExportAllDeclaration(node) {
        checkSource(context, node, node.source.value);
      },
      ImportExpression(node) {
        if (node.source?.type === "Literal") checkSource(context, node, node.source.value);
      },
      CallExpression(node) {
        if (node.callee?.type === "Identifier" && node.callee.name === "require" && node.arguments[0]?.type === "Literal") {
          checkSource(context, node, node.arguments[0].value);
        }
      },
    };
  },
};

module.exports = rule;

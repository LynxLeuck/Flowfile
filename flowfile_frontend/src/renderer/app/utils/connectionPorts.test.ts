import { describe, expect, it } from "vitest";
import { Position } from "@vue-flow/core";
import { connectionPortError } from "./connectionPorts";

const connection = {
  source: "1",
  target: "2",
  sourceHandle: "output-0",
  targetHandle: "input-0",
};
const source = { outputs: [{ id: "output-0", position: Position.Right }] };
const target = {
  inputs: [{ id: "input-0", position: Position.Left, multiple: false }],
  nodeTemplate: { name: "Target", multi: true },
};
const occupied = [{ ...connection, source: "3" }];

describe("connection port validation", () => {
  it("rejects handles absent from the effective instance ports", () => {
    expect(connectionPortError(connection, { outputs: [] }, target, [])).not.toBeNull();
    expect(connectionPortError(connection, source, { ...target, inputs: [] }, [])).not.toBeNull();
  });

  it("uses per-port multiplicity rather than the legacy node-wide flag", () => {
    expect(connectionPortError(connection, source, target, occupied)).not.toBeNull();
    expect(
      connectionPortError(
        connection,
        source,
        {
          ...target,
          inputs: [{ ...target.inputs[0], multiple: true }],
          nodeTemplate: { name: "Target", multi: false },
        },
        occupied,
      ),
    ).toBeNull();
  });

  it("does not count the same restored connection as occupying its handle", () => {
    expect(connectionPortError(connection, source, target, [connection])).toBeNull();
  });

  it("keeps the legacy fallback for handle snapshots without metadata", () => {
    const legacy = {
      inputs: [{ id: "input-0", position: Position.Left }],
      nodeTemplate: { name: "Union", multi: true },
    };
    expect(connectionPortError(connection, source, legacy, occupied)).toBeNull();
    expect(
      connectionPortError(
        connection,
        source,
        {
          ...legacy,
          nodeTemplate: { ...legacy.nodeTemplate, dynamic_inputs: true },
        },
        occupied,
      ),
    ).not.toBeNull();
  });
});

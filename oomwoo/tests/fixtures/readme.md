# OOMWOO

*Open-source robot vacuum you build yourself*

![Status](https://img.shields.io/badge/status-early%20development-orange)

*v0 target: bare-bones build:*

- 3D-printed chassis ([browse](https://github.com/makerspet/oomwoo-install))
- ROS2 Gazebo sim ([install](https://github.com/makerspet/oomwoo-install))
- Basic cleaning, mapping
- Raspberry Pi CM4/CM5 running ROS2 ([install](https://github.com/makerspet/oomwoo-install))

Open Source Deliverables:

- [x] [Software development environment](https://github.com/makerspet/oomwoo-install)
- [x] Placeholder real [vacuum cleaner](https://github.com/makerspet/proscenic-m6pro)
- [x] [Bill of materials (BoM)](BOM.md)
- [x] 3D-scanned [sourced parts](https://github.com/makerspet/oomwoo-one-cad/tree/main/lib)
- [ ] 3D-printable [files](https://github.com/makerspet/oomwoo-one-cad)
- [ ] Raspberry Pi [software](https://github.com/makerspet/oomwoo-install)
- [ ] Motor drivers, sensors [PCB boards](https://github.com/makerspet/oomwoo-pcb)
- [ ] I/O PCB [firmware](https://github.com/makerspet/oomwoo-io-firmware)
- [ ] Build, setup, bringup and troubleshooting [instructions](docs/BUILD_INSTRUCTIONS.md)
- [ ] Demo video(s)

## Requests for Contributions

| Module | ID | Status | Notes |
|---|---|---|---|
| ROS2 URDF + Gazebo sim | [urdf-gazebo-sim](./contributions/urdf-gazebo-sim) | Mostly complete | Placeholder URDF + Gazebo sim |
| First clean: coverage + mapping + exploration | [clean-and-map](./contributions/clean-and-map) | In progress | Coverage cleaning while SLAM-mapping |
| Auto cleaning |  | In progress | Clean the entire room using an existing map |
| Regression tests |  | In progress | Set up simulatior regression test framework |
| Dock cycle: undock, dock, recharge | [dock-cycle](./contributions/dock-cycle) | Ready to start work | Undock, return-to-dock |
| Compute benchmark & memory reduction | [compute-benchmark](./contributions/compute-benchmark) | In progress | Measure ROS2/Nav2/SLAM memory |
| Source 3D models (STEP) for BOM parts | [source-3d-models](./contributions/source-3d-models) | Mostly complete | STEP files of off-the-shelf parts |
| Fit software into 2GB RAM | [compute-benchmark](./contributions/compute-benchmark) | 2GB achieved | ROS2 node composition, Rust |

> Planned modules live in the RFC backlog.

## License

Apache License 2.0.

{
  description = "Python application managed with poetry2nix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
    flake-utils = { url = "github:numtide/flake-utils"; };
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix, flake-compat }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [
            poetry2nix.overlay
          ];
        };
        python = pkgs.python310;
        packageName = "label-approved";
        packageVersion = "0.1.0";
      in
      {
        packages = rec {
          label-approved = python.pkgs.buildPythonApplication rec {
            pname = packageName;
            version = packageVersion;
            format = "pyproject";
            nativeBuildInputs = with python.pkgs; [ poetry-core ];
            propagatedBuildInputs = with python.pkgs; [ PyGithub dateutil ];
            src = ./.;
            nativeCheckInputs = with pkgs; [ python.pkgs.mypy python.pkgs.types-dateutil ];
            checkPhase = ''
              export MYPYPATH=$PWD/src
              mypy --strict .
            '';
          };
          default = label-approved;
        };

        devShells = {
          default = pkgs.mkShell {
            buildInputs = with pkgs; [
              pyright
              (pkgs.poetry.override { python = python; })
              (pkgs.poetry2nix.mkPoetryEnv {
                inherit python;
                projectDir = ./.;
                overrides = pkgs.poetry2nix.overrides.withDefaults (self: super: { });
                editablePackageSources = {
                  label-approved = ./src;
                };
                extraPackages = (ps: with ps; [
                ]);
              })
            ] ++ (with python.pkgs; [
              black
              pylint
              mypy
            ]);
            shellHook = ''
              export MYPYPATH=$PWD/src
            '';
          };
        };

      }) // {
        nixosModule = { config, lib, pkgs, ... }: let
          cfg = config.services.label-approved;
        in {
          options.services.label-approved = with lib; {
            enable = mkEnableOption "Enables the approved PRs labeler service.";
            interval = mkOption {
              default = "*:0/30";
              type = types.str;
              description = lib.mdDoc "systemd-timer OnCalendar config";
            };
            environmentFile = mkOption {
              type = types.path;
              example = "/run/secrets/label-approved.env";
              description = lib.mdDoc ''
                Environment file to source before running the service. This
                should contain a GITHUB_TOKEN or GITHUB_BOT_TOKEN variable.
              '';
            };
          };

          config = lib.mkIf cfg.enable {
            systemd.timers.label-approved = {
              wantedBy = [ "timers.target" ];
              after = [ "multi-user.target" ];
              timerConfig.OnCalendar = cfg.interval;
            };
            systemd.services.label-approved = {
              description = "label-approved service";
              after = [ "network-online.target" ];
              wants = [ "network-online.target" ];
              serviceConfig = {
                DynamicUser = true;
                EnvironmentFile = cfg.environmentFile;
                ExecStart = "${self.packages.${pkgs.system}.label-approved}/bin/label-approved";
              };
            };
          };
        };
      };
}

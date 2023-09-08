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

      });
}

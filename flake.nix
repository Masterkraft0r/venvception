{
  description = "One flake to rule them all";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = (
    {
      flake-utils,
      nixpkgs,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (system: {
      devShells.default =
        let
          pkgs = import nixpkgs {
            inherit system;

            config = {
              allowUnfree = true;
              permittedInsecurePackages = [
                "segger-jlink-qt4-810"
              ];
              segger-jlink.acceptLicense = true;
            };
          };
        in
        pkgs.mkShell {
          name = "ng-master-app";
          packages = with pkgs; [
            basedpyright
            just
            python312
            ruff
            trivy
            uv
          ];
        };
    })
  );
}

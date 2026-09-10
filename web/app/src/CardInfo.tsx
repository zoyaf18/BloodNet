/** A compact tooltip that explains the operational purpose of a card. */
export function CardInfo({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <span className="card-info">
      <button
        type="button"
        aria-label={`${title}: ${description}`}
        onClick={(event) => event.currentTarget.blur()}
      >
        i
      </button>
      <span className="card-info-popover" role="tooltip">
        <p>{description}</p>
      </span>
    </span>
  );
}
